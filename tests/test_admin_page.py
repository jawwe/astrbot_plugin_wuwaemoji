import asyncio
import base64
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
from admin_page import AdminPage
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
        assert len(routes) == 11
        assert all(
            path.startswith("/astrbot_plugin_wuwaemoji/admin/") for path, *_ in routes
        )
        for action in ("overview", "library", "preview", "jobs/status", "site/catalog"):
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


def test_site_catalog_is_live_paginated_cached_and_refreshable(plugin):
    async def run():
        plugin.request_api = AsyncMock(
            return_value={
                "items": [
                    {
                        "slug": "aimisi",
                        "name": "爱弥斯",
                        "count": 100,
                        "packCount": 4,
                        "url": "SIGNED-PRIVATE",
                    }
                ],
                "page": 1,
                "total": 130,
                "hasNext": True,
            }
        )
        code, result = await call(plugin, "site/catalog", query={"kind": "characters"})
        assert (
            code == 200
            and result["items"][0]["name"] == "爱弥斯"
            and result["has_next"]
        )
        assert result["items"][0]["pack_count"] == 4 and "SIGNED-PRIVATE" not in str(
            result
        )
        await call(plugin, "site/catalog", query={"kind": "characters"})
        assert plugin.request_api.await_count == 1
        await call(plugin, "site/catalog", query={"kind": "characters", "refresh": "1"})
        await call(
            plugin, "site/catalog", query={"kind": "artists", "page": 2, "q": "雾"}
        )
        assert plugin.request_api.await_count == 3
        assert plugin.request_api.call_args.args == (
            "/artists",
            {"q": "雾", "page": 2, "size": 100, "sort": "count", "order": "desc"},
        )
        code, _ = await call(
            plugin, "site/catalog", query={"kind": "https://evil.test"}
        )
        assert code == 400 and plugin.request_api.await_count == 3

    asyncio.run(run())


def test_packs_follow_selected_categories_without_image_tickets(plugin):
    async def run():
        plugin.request_api = AsyncMock(
            return_value={
                "items": [
                    {
                        "id": "pack-id",
                        "title": "问候表情包",
                        "count": 8,
                        "url": "SIGNED-PRIVATE",
                        "characters": [{"name": "爱弥斯"}],
                        "artists": [],
                    }
                ],
                "total": 1,
                "hasNext": False,
            }
        )
        code, result = await call(
            plugin,
            "site/packs",
            {
                "filters": {"character": "爱弥斯", "artist": "雾雪", "pack": "ignored"},
                "page": 2,
            },
        )
        assert code == 200 and result["items"][0]["id"] == "pack-id"
        assert "SIGNED-PRIVATE" not in str(result)
        assert plugin.request_api.call_args.args == (
            "/packs",
            {
                "character": "爱弥斯",
                "artist": "雾雪",
                "page": 2,
                "size": 20,
                "sort": "newest",
            },
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "format,mime", [("GIF", "image/gif"), ("WEBP", "image/webp"), ("PNG", "image/png")]
)
def test_animation_preview_keeps_frames_duration_and_original_bytes(
    plugin, format, mime
):
    async def run():
        content = io.BytesIO()
        first, second = (
            Image.new("RGBA", (20, 20), "red"),
            Image.new("RGBA", (20, 20), "blue"),
        )
        first.save(
            content,
            format,
            save_all=True,
            append_images=[second],
            duration=[100, 200],
            loop=0,
        )
        original = content.getvalue()
        saved = await plugin.cache.put(item(), original, "开心")
        assert saved["animated"]
        code, result = await call(plugin, "preview", query={"id": saved["id"]})
        assert code == 200 and result["animated"]
        assert result["data_url"].startswith(f"data:{mime};base64,")
        data = base64.b64decode(result["data_url"].split(",", 1)[1])
        assert data == original
        with Image.open(io.BytesIO(data)) as decoded:
            assert decoded.n_frames == 2
        assert result["poster_url"].startswith("data:image/webp;base64,")
        assert plugin.admin.preview_bytes > len(original)

    asyncio.run(run())


def test_pack_job_pages_follow_pack_size_instead_of_query_page_cap(plugin):
    async def run():
        plugin.request_api = AsyncMock(
            return_value={"items": [item("a"), item("b")], "hasNext": True}
        )
        plugin.cache_image = AsyncMock(return_value="local-file")
        body = {
            "mode": "pack",
            "pack": "pack-id",
            "maximum": 12,
            "category": "未分类",
            "request_id": "pack-test",
        }
        code, job = await call(plugin, "jobs/start", body)
        assert code == 200 and job["pages"] == 6
        await plugin.admin.task
        assert (
            plugin.request_api.await_count == 6 and plugin.cache_image.await_count == 12
        )
        for invocation in plugin.request_api.call_args_list:
            assert (
                invocation.args[0] == "/emojis"
                and invocation.args[1]["pack"] == "pack-id"
            )
        _, final = await call(plugin, "jobs/status", query={"id": job["id"]})
        assert final["success"] == 12 and final["state"] == "completed"
        code, _ = await call(
            plugin, "jobs/start", body | {"request_id": "pack-invalid", "maximum": 101}
        )
        assert code == 400

    asyncio.run(run())


def test_large_animated_preview_reports_limit_without_flattening():
    content = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(
        content,
        "GIF",
        save_all=True,
        append_images=[Image.new("RGB", (8, 8), "blue")],
        duration=100,
        loop=0,
    )
    with pytest.raises(CacheError, match="8 MiB"):
        AdminPage.thumbnail(content.getvalue() + bytes(8 * 1024 * 1024))


def test_pack_pagination_keeps_page_size_fixed_on_partial_final_page(plugin):
    async def run():
        async def api(endpoint, params):
            size = params["size"]
            offset = (params["page"] - 1) * size
            return {
                "items": [item(str(index)) for index in range(offset, offset + size)],
                "hasNext": True,
            }

        plugin.request_api = AsyncMock(side_effect=api)
        plugin.cache_image = AsyncMock(return_value="local-file")
        code, job = await call(
            plugin,
            "jobs/start",
            {
                "mode": "pack",
                "pack": "one-pack",
                "maximum": 5,
                "request_id": "fixed-pages",
            },
        )
        assert code == 200
        await plugin.admin.task
        assert [
            entry.args[1]["size"] for entry in plugin.request_api.call_args_list
        ] == [2, 2, 2]
        assert [entry.args[0]["id"] for entry in plugin.cache_image.call_args_list] == [
            "0",
            "1",
            "2",
            "3",
            "4",
        ]

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
