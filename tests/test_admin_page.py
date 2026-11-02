import asyncio
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from astrbot.api.web import PluginRequest, bind_request_context
from PIL import Image
from starlette.requests import Request

import main
from emoji_cache import CacheError


def picture(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (18, 18), color).save(buffer, "PNG")
    return buffer.getvalue()


def item(identifier="asset-1"):
    return {
        "id": identifier,
        "url": "https://emoji.wuwa.games/media?ticket=SIGNED-PRIVATE",
        "format": "png",
        "pack": {"title": "爱弥斯表情"},
        "characters": [{"name": "爱弥斯", "slug": "aimisi"}],
    }


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(main.StarTools, "get_data_dir", lambda name: tmp_path)
    return main.WuwaEmojiPlugin(
        None, {"api_token": "TOKEN-PRIVATE", "cache_batch_size": 2}
    )


async def call(plugin, action, body=None, query=None, owner="admin"):
    content = json.dumps(body).encode() if body is not None else b""

    async def receive():
        return {"type": "http.request", "body": content}

    scope = {
        "type": "http",
        "method": "POST" if body is not None else "GET",
        "path": "/test",
        "query_string": urlencode(query or {}).encode(),
        "headers": [(b"content-type", b"application/json")],
        "server": ("localhost", 80),
        "scheme": "http",
    }
    with bind_request_context(PluginRequest(Request(scope, receive), username=owner)):
        response = await plugin.admin.handle(action)
    return response.status_code, json.loads(response.body)


def test_registration_and_unauthenticated_access(plugin):
    async def run():
        context = SimpleNamespace(register_web_api=lambda *args: routes.append(args))
        routes = []
        plugin.admin.register(context)
        assert len(routes) == 9
        assert all(
            path.startswith("/astrbot_plugin_wuwaemoji/admin/") for path, *_ in routes
        )
        for action in ("overview", "library", "preview", "jobs/status"):
            code, _ = await call(plugin, action, owner=None)
            assert code == 401
        code, _ = await call(
            plugin,
            "categories",
            {"name": "任意", "description": "不能创建"},
            owner=None,
        )
        assert code == 401
        assert not (plugin.cache.root / "index.json").exists()

    asyncio.run(run())


def test_library_filters_preview_and_secret_projection(plugin):
    async def run():
        saved = await plugin.cache.put(item(), picture(), "开心")
        code, result = await call(plugin, "overview")
        assert code == 200 and result["token_set"]
        assert "TOKEN-PRIVATE" not in str(result)
        code, result = await call(
            plugin, "library", query={"category": "开心", "character": "爱弥斯"}
        )
        assert code == 200 and result["total"] == 1
        assert result["items"][0]["id"] == saved["id"]
        assert "SIGNED-PRIVATE" not in str(result) and "path" not in result["items"][0]
        code, preview = await call(plugin, "preview", query={"id": saved["id"]})
        assert code == 200 and preview["data_url"].startswith("data:image/webp;base64,")
        assert saved["path"] not in str(preview)
        code, result = await call(plugin, "library", query={"character": "漂泊者"})
        assert code == 200 and result["items"] == []
        code, _ = await call(plugin, "preview", query={"id": "../../token"})
        assert code == 400

    asyncio.run(run())


def test_search_snapshots_are_owned_expire_and_do_not_cache(plugin):
    async def run():
        plugin.request_api = AsyncMock(return_value={"items": [item()], "total": 1})
        code, result = await call(
            plugin, "search", {"filters": {"character": "爱弥斯"}, "page": 1}
        )
        assert code == 200 and "SIGNED-PRIVATE" not in str(result)
        assert (await plugin.cache.catalog())["count"] == 0
        snapshot, identifier = result["snapshot"], result["items"][0]["id"]
        plugin.download_image = AsyncMock(return_value=picture())
        code, _ = await call(
            plugin,
            "preview",
            query={"source": "remote", "snapshot": snapshot, "id": identifier},
            owner="someone-else",
        )
        assert code == 400
        plugin.download_image.assert_not_called()
        code, _ = await call(
            plugin,
            "preview",
            query={"source": "remote", "snapshot": snapshot, "id": identifier},
        )
        assert code == 200 and (await plugin.cache.catalog())["count"] == 0
        plugin.admin.snapshots[snapshot]["expires"] = time.monotonic() - 1
        code, _ = await call(
            plugin,
            "preview",
            query={"source": "remote", "snapshot": snapshot, "id": identifier},
        )
        assert code == 400

    asyncio.run(run())


def test_selected_job_idempotency_partial_success_and_ownership(plugin):
    async def run():
        plugin.request_api = AsyncMock(
            return_value={"items": [item("a"), item("b")], "total": 2}
        )
        _, search = await call(plugin, "search", {"filters": {}})
        started = asyncio.Event()
        proceed = asyncio.Event()

        async def cache(record, category, required):
            started.set()
            await proceed.wait()
            if record["id"] == "b":
                raise CacheError("测试下载失败")
            return "local-file"

        plugin.cache_image = AsyncMock(side_effect=cache)
        body = {
            "mode": "selected",
            "snapshot": search["snapshot"],
            "ids": [x["id"] for x in search["items"]],
            "category": "开心",
            "request_id": "once",
        }
        code, job = await call(plugin, "jobs/start", body)
        assert code == 200 and job["state"] == "running"
        await started.wait()
        _, duplicate = await call(plugin, "jobs/start", body)
        assert duplicate["id"] == job["id"]
        code, _ = await call(plugin, "jobs/start", body | {"request_id": "other"})
        assert code == 409
        code, _ = await call(
            plugin, "jobs/status", query={"id": job["id"]}, owner="other"
        )
        assert code == 404
        proceed.set()
        await plugin.admin.task
        _, final = await call(plugin, "jobs/status", query={"id": job["id"]})
        assert (
            final["state"] == "completed"
            and final["success"] == 1
            and final["failed"] == 1
        )
        assert plugin.cache_image.await_count == 2
        assert "TOKEN-PRIVATE" not in str(final) and "SIGNED-PRIVATE" not in str(final)

    asyncio.run(run())


def test_query_job_limits_and_cooperative_cancellation(plugin):
    async def run():
        plugin.request_api = AsyncMock(
            return_value={"items": [item("a"), item("b")], "hasNext": True}
        )
        started = asyncio.Event()
        proceed = asyncio.Event()

        async def cache(*args, **kwargs):
            started.set()
            await proceed.wait()

        plugin.cache_image = AsyncMock(side_effect=cache)
        body = {
            "mode": "query",
            "filters": {"character": "爱弥斯"},
            "pages": 5,
            "maximum": 3,
            "category": "未分类",
            "request_id": "cancel",
        }
        _, job = await call(plugin, "jobs/start", body)
        await started.wait()
        code, cancellation = await call(plugin, "jobs/cancel", {"id": job["id"]})
        assert code == 200 and cancellation["cancel_requested"]
        proceed.set()
        await plugin.admin.task
        _, result = await call(plugin, "jobs/status", query={"id": job["id"]})
        assert result["state"] == "cancelled" and result["success"] == 1
        assert (
            plugin.request_api.await_count == 1 and plugin.cache_image.await_count == 1
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"pages": 6},
        {"pages": 0},
        {"maximum": 101},
        {"maximum": True},
        {"start_page": "1.5"},
        {"filters": {}},
        {"filters": {"character": []}},
        {"category": "角色：爱弥斯"},
        {"mode": "url", "url": "https://evil.test"},
    ],
)
def test_reject_invalid_jobs_before_network(plugin, changes):
    async def run():
        plugin.request_api = AsyncMock()
        base = {
            "mode": "query",
            "filters": {"character": "爱弥斯"},
            "pages": 1,
            "maximum": 4,
            "category": "未分类",
            "request_id": "invalid",
        }
        code, _ = await call(plugin, "jobs/start", base | changes)
        assert code == 400 and plugin.admin.task is None
        plugin.request_api.assert_not_called()

    asyncio.run(run())


def test_bounded_query_does_not_exceed_total_and_reports_api_failure(plugin):
    async def run():
        plugin.request_api = AsyncMock(
            side_effect=[
                {"items": [item("a"), item("b")], "hasNext": True},
                RuntimeError("SIGNED-PRIVATE"),
            ]
        )
        plugin.cache_image = AsyncMock(return_value="local")
        body = {
            "mode": "query",
            "filters": {"character": "爱弥斯"},
            "pages": 5,
            "maximum": 5,
            "category": "开心",
            "request_id": "partial",
        }
        _, job = await call(plugin, "jobs/start", body)
        await plugin.admin.task
        _, status = await call(plugin, "jobs/status", query={"id": job["id"]})
        assert status["state"] == "failed" and status["success"] == 2
        assert "SIGNED-PRIVATE" not in str(status)
        plugin.request_api = AsyncMock(
            return_value={"items": [item("a"), item("b")], "hasNext": True}
        )
        _, job = await call(
            plugin, "jobs/start", body | {"maximum": 1, "request_id": "bounded"}
        )
        await plugin.admin.task
        _, status = await call(plugin, "jobs/status", query={"id": job["id"]})
        assert status["done"] == 1

    asyncio.run(run())


def test_classification_validates_all_ids_before_mutation(plugin):
    async def run():
        record = await plugin.cache.put(item(), picture())
        code, _ = await call(
            plugin, "classify", {"ids": [record["id"], "missing"], "category": "开心"}
        )
        assert code == 400
        assert "开心" not in (await plugin.cache.get(record["id"]))["categories"]
        code, response = await call(
            plugin,
            "classify",
            {"ids": [record["id"]], "category": "开心", "description": "笑脸"},
        )
        assert code == 200 and response["saved"] == 1
        assert (await plugin.cache.get(record["id"]))["description"] == "笑脸"

    asyncio.run(run())
