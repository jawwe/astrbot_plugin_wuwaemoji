import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

import main
from emoji_cache import CacheError, EmojiCache


def image_bytes(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (12, 12), color).save(buffer, format="PNG")
    return buffer.getvalue()


def record(identifier="asset-1", name="爱弥斯"):
    return {
        "id": identifier,
        "url": "https://emoji.wuwa.games/media?ticket=private-signed",
        "format": "png",
        "characters": [
            {"name": name, "slug": "aimisi" if name == "爱弥斯" else "rover"}
        ],
        "artists": [{"name": "画师", "slug": "artist"}],
        "pack": {"title": f"{name}表情包"},
        "sourceUrl": "https://emoji.wuwa.games/archives/example?ticket=private-signed",
    }


def event(text="", admin=False, user="a", origin="test:group:1"):
    return SimpleNamespace(
        get_message_str=lambda: text,
        get_sender_id=lambda: user,
        unified_msg_origin=origin,
        is_admin=lambda: admin,
        plain_result=lambda text: text,
        image_result=lambda path: {"image": path},
        chain_result=lambda chain: chain,
        send=AsyncMock(),
    )


async def collect(generator):
    return [x async for x in generator]


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(main.StarTools, "get_data_dir", lambda name: tmp_path)
    return main.WuwaEmojiPlugin(None, {})


def test_persistence_dedup_parallel_dimensions_and_no_tickets(tmp_path):
    async def run():
        cache = EmojiCache(tmp_path)
        first = await cache.put(record(), image_bytes())
        second = await cache.put(record(), image_bytes(), "开心")
        assert first["id"] == second["id"]
        assert len(list((tmp_path / "images").iterdir())) == 1
        assert "未分类" not in second["categories"]
        assert {"开心", "角色：爱弥斯"} <= set(second["categories"])
        await cache.classify(first["id"], "赞同", "开心地竖起大拇指，表示赞同")
        restored = EmojiCache(tmp_path)
        assert len(await restored.search(category="开心", character="aimisi")) == 1
        assert len(await restored.search(category="赞同", character="爱弥斯")) == 1
        assert await restored.search(category="开心", character="漂泊者") == []
        assert await restored.search(query="大拇指")
        catalog = await restored.catalog()
        assert catalog["count"] == 1
        assert (
            next(x for x in catalog["categories"] if x["name"] == "角色：爱弥斯")[
                "source"
            ]
            == "upstream_article_tag"
        )
        stored = (tmp_path / "index.json").read_text(encoding="utf-8")
        assert "private-signed" not in stored and "ticket" not in stored
        assert "https://emoji.wuwa.games/archives/example" in stored

    asyncio.run(run())


def test_limits_clear_and_missing_files(tmp_path):
    async def run():
        cache = EmojiCache(tmp_path, max_items=1)
        item = await cache.put(record(), image_bytes(), "开心")
        with pytest.raises(CacheError, match="已满"):
            await cache.put(record("asset-2"), image_bytes("blue"))
        assert await cache.clear("开心") == 1
        assert await cache.get(item["id"])
        assert await cache.clear("全部") == 1
        assert not await cache.get(item["id"])
        assert not Path(item["path"]).exists()
        await cache.put(record(), image_bytes())
        Path(item["path"]).unlink()
        assert not await cache.search()
        assert (await cache.catalog())["count"] == 0

    asyncio.run(run())


def test_parallel_duplicate_writes(tmp_path):
    async def run():
        cache = EmojiCache(tmp_path)
        result = await asyncio.gather(
            *(
                cache.put(record(), image_bytes(), name)
                for name in ["开心", "赞同", "问候"]
            )
        )
        assert len({x["id"] for x in result}) == 1
        stored = await cache.get(result[0]["id"])
        assert {"开心", "赞同", "问候"} <= set(stored["categories"])
        assert (await cache.catalog())["count"] == 1

    asyncio.run(run())


def test_content_shared_between_characters_retains_curated_description(tmp_path):
    async def run():
        cache = EmojiCache(tmp_path)
        first = await cache.put(record(), image_bytes(), "开心")
        await cache.classify(first["id"], "赞同", "笑着点赞")
        shared = await cache.put(record("asset-2", "漂泊者"), image_bytes())
        assert shared["description"] == "笑着点赞"
        assert {"角色：爱弥斯", "角色：漂泊者", "开心", "赞同"} <= set(
            shared["categories"]
        )
        assert await cache.search(category="开心", character="漂泊者")
        assert (await cache.catalog())["count"] == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "name", ["../abc", "x/y", "x\\y", "", "全部", "角色：爱弥斯", "x" * 41]
)
def test_category_names_are_not_paths(tmp_path, name):
    with pytest.raises(CacheError):
        asyncio.run(EmojiCache(tmp_path).set_category(name, "描述"))


def test_rejects_invalid_media_and_corrupt_index(tmp_path):
    async def run():
        cache = EmojiCache(tmp_path)
        with pytest.raises(CacheError, match="图片"):
            await cache.put(record(), b"<html>not an image</html>")
        with pytest.raises(CacheError, match="不存在"):
            await cache.put(record(), image_bytes(), "unknown")
        (tmp_path / "index.json").write_text("[]", encoding="utf-8")
        with pytest.raises(CacheError, match="索引损坏"):
            await EmojiCache(tmp_path).catalog()

    asyncio.run(run())


def test_index_cannot_resolve_outside_images(tmp_path):
    async def run():
        cache = EmojiCache(tmp_path)
        item = await cache.put(record(), image_bytes())
        index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
        index["items"][item["id"]]["file"] = "../outside.png"
        (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
        with pytest.raises(CacheError, match="路径无效"):
            await EmojiCache(tmp_path).catalog()

    asyncio.run(run())


def test_tools_search_send_filter_cooldown_and_hints(plugin):
    async def run():
        item = await plugin.cache.put(record(), image_bytes(), "开心")
        e = event()
        data = json.loads(
            await plugin.search_emojis_tool(e, category="开心", character="爱弥斯")
        )
        assert data["items"][0]["id"] == item["id"]
        assert "path" not in data["items"][0] and "private-signed" not in str(data)
        assert "不满足" in await plugin.send_emoji_tool(
            e, emoji_id=item["id"], character="漂泊者"
        )
        assert "不存在" in await plugin.send_emoji_tool(e, emoji_id="../../outside.png")
        assert "已向当前聊天" in await plugin.send_emoji_tool(
            e, emoji_id=item["id"], category="开心", character="aimisi"
        )
        e.send.assert_awaited_once_with({"image": item["path"]})
        assert "冷却" in await plugin.send_emoji_tool(e, category="开心")
        req = main.ProviderRequest(system_prompt="keep persona")
        await plugin.category_hints(e, req)
        await plugin.category_hints(e, req)
        assert req.system_prompt == "keep persona"
        assert len(req.extra_user_content_parts) == 1
        assert "开心" in req.extra_user_content_parts[0].text
        assert item["path"] not in req.extra_user_content_parts[0].text
        assert (
            len(await collect(plugin.local_emoji(event("鸣潮本地 开心 角色=爱弥斯"))))
            == 1
        )

    asyncio.run(run())


def test_tool_failure_disabled_and_no_false_success(plugin):
    async def run():
        await plugin.cache.put(record(), image_bytes(), "开心")
        e = event()
        e.send.side_effect = RuntimeError("private-signed")
        result = await plugin.send_emoji_tool(e, category="开心")
        assert "发送失败" in result and "private-signed" not in result
        assert not plugin.tool_pending and not plugin.tool_sends
        plugin.config["enable_llm_tools"] = False
        assert "关闭" in await plugin.send_emoji_tool(e, category="开心")
        assert "关闭" in await plugin.search_emojis_tool(e)
        assert e.send.await_count == 1
        req = main.ProviderRequest()
        await plugin.category_hints(e, req)
        assert not req.extra_user_content_parts

    asyncio.run(run())


def test_empty_emotion_does_not_fall_back_to_wrong_character(plugin):
    async def run():
        plugin.request_api = AsyncMock()
        result = await plugin.send_emoji_tool(
            event(), category="难过", character="爱弥斯"
        )
        assert "暂无" in result
        plugin.request_api.assert_not_called()

    asyncio.run(run())


def test_library_permissions_and_curation(plugin):
    async def run():
        result = (await collect(plugin.library(event("鸣潮图库 分类 高兴 快乐"))))[0]
        assert "仅限" in result
        assert not (plugin.cache.root / "index.json").exists()
        assert (
            "已保存"
            in (
                await collect(
                    plugin.library(event("鸣潮图库 分类 高兴 适合表达快乐", admin=True))
                )
            )[0]
        )
        item = await plugin.cache.put(record(), image_bytes())
        result = await collect(
            plugin.library(
                event(f"鸣潮图库 归类 {item['id']} 高兴 开心笑脸", admin=True)
            )
        )
        assert "已更新" in result[0]
        assert await plugin.cache.search(category="高兴", character="爱弥斯")
        result = await collect(plugin.library(event("鸣潮图库 清空 全部", admin=True)))
        assert "1 张" in result[0]
        assert (await plugin.cache.catalog())["count"] == 0

    asyncio.run(run())


class Response:
    def __init__(self, content=b"", status=200, location=""):
        self.status = status
        self.headers = {"Location": location}
        self.content = self
        self.body = content

    async def iter_chunked(self, size):
        yield self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class Session:
    closed = False

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)

    async def close(self):
        self.closed = True


def test_media_redirect_contract_dedup_and_no_credentials(plugin):
    async def run():
        plugin.config["api_token"] = "private-token"
        plugin.session = Session(
            [
                Response(status=307, location="https://media.wuwa.games/image.png"),
                Response(image_bytes()),
            ]
        )
        path = await plugin.cache_image(record(), required=True)
        assert Path(path).is_file()
        assert len(plugin.session.calls) == 2
        for _, request in plugin.session.calls:
            assert "Authorization" not in request["headers"]
            assert not request["allow_redirects"]
        assert await plugin.cache_image(record(), "开心", required=True) == path
        assert len(plugin.session.calls) == 2
        assert await plugin.cache.search(category="开心")

    asyncio.run(run())


def test_media_rejects_external_redirect_and_size_and_falls_back(plugin):
    async def run():
        plugin.session = Session(
            [Response(status=307, location="https://evil.example/steal")]
        )
        with pytest.raises(main.WuwaAPIError):
            await plugin.cache_image(record(), required=True)
        assert len(plugin.session.calls) == 1
        plugin.config["cache_image_limit_mb"] = 1
        plugin.session = Session([Response(b"x" * (1024 * 1024 + 1))])
        with pytest.raises(CacheError, match="大小限制"):
            await plugin.cache_image(record(), required=True)
        plugin.session = Session([Response(status=567)])
        assert await plugin.cache_image(record()) == record()["url"]

    asyncio.run(run())
