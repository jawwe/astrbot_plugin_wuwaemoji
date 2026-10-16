import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

import main


def event(text, user="user1", origin="test:group:1"):
    return SimpleNamespace(
        get_message_str=lambda: text,
        get_sender_id=lambda: user,
        unified_msg_origin=origin,
        plain_result=lambda text: text,
        image_result=lambda url: {"image": url},
        chain_result=lambda chain: chain,
    )


async def collect(generator):
    return [result async for result in generator]


def plugin(config=None):
    return main.WuwaEmojiPlugin(None, config or {})


def test_parse_filters_and_spaces():
    assert main.parse_query('爱弥斯 画师="Some Artist" 页码=2', paginated=True) == {
        "character": "爱弥斯",
        "artist": "Some Artist",
        "page": 2,
    }
    assert main.parse_query("角色=爱弥斯,漂泊者 匹配=or") == {
        "character": "爱弥斯,漂泊者",
        "match": "or",
    }
    assert main.parse_query("关键词=慵懒", paginated=True) == {"q": "慵懒"}


@pytest.mark.parametrize(
    "text",
    [
        "页码=0",
        "页码=-2",
        "页码=abc",
        "页码=100001",
        "unknown=x",
        "角色=",
        "匹配=x",
        "角色=爱弥斯 爱弥斯",
        "角色=爱弥斯 character=rover",
        '画师="abc',
    ],
)
def test_invalid_filters(text):
    with pytest.raises(ValueError):
        main.parse_query(text, paginated=True)


@pytest.mark.parametrize("text", ["关键词=慵懒", "页码=2"])
def test_random_rejects_unsupported_options(text):
    with pytest.raises(ValueError):
        main.parse_query(text)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "http://emoji.wuwa.games/x",
        "https://evil.test/a",
        "https://user:pass@emoji.wuwa.games/a",
    ],
)
def test_invalid_image_urls(url):
    with pytest.raises(main.WuwaAPIError):
        main.image_url({"url": url})


def test_search_pagination_selection_and_isolation():
    async def run():
        p = plugin({"page_size": 1})
        p.request_api = AsyncMock(
            return_value={
                "items": [
                    {
                        "url": "https://emoji.wuwa.games/media?ticket=test",
                        "pack": {"title": "表情"},
                    }
                ],
                "total": 2,
                "hasNext": True,
            }
        )
        results = await collect(
            p.search_emoji(event('鸣潮搜图 爱弥斯 画师="Some Artist"'))
        )
        chain = results[0]
        assert sum(isinstance(x, main.Image) for x in chain) == 1
        tip = chain[-1].text
        args = tip.split("下一页：/鸣潮搜图 ")[1]
        assert main.parse_query(args, paginated=True) == {
            "character": "爱弥斯",
            "artist": "Some Artist",
            "page": 2,
        }
        assert p.request_api.call_args.args == (
            "/emojis",
            {
                "character": "爱弥斯",
                "artist": "Some Artist",
                "format": "webp",
                "size": 1,
                "page": 1,
            },
        )
        assert (await collect(p.select_emoji(event("鸣潮取图 1"))))[0] == {
            "image": "https://emoji.wuwa.games/media?ticket=test",
        }
        for other in [
            event("鸣潮取图 1", user="user2"),
            event("鸣潮取图 1", origin="test:group:2"),
        ]:
            assert "不存在" in (await collect(p.select_emoji(other)))[0]
        assert "序号" in (await collect(p.select_emoji(event("鸣潮取图 -1"))))[0]
        p.searches[("test:group:1", "user1")] = (time.monotonic() - 1, ["old"])
        assert "已过期" in (await collect(p.select_emoji(event("鸣潮取图 1"))))[0]

    asyncio.run(run())


def test_cache_bound_and_termination():
    async def run():
        p = plugin()
        p.request_api = AsyncMock(
            return_value={"items": [{"url": "https://emoji.wuwa.games/media"}]}
        )
        for i in range(130):
            await collect(p.search_emoji(event("鸣潮搜图 爱弥斯", user=str(i))))
        assert len(p.searches) == 128
        assert ("test:group:1", "0") not in p.searches
        await p.terminate()
        assert not p.searches

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"items": "bad"},
        {"items": [None]},
        {"items": [{"url": "https://evil.test"}]},
    ],
)
def test_malformed_search_response(payload):
    async def run():
        p = plugin()
        p.request_api = AsyncMock(return_value=payload)
        result = (await collect(p.search_emoji(event("鸣潮搜图 爱弥斯"))))[0]
        assert isinstance(result, str)
        assert not p.searches

    asyncio.run(run())


def test_help_empty_search_and_empty_results():
    async def run():
        p = plugin()
        p.request_api = AsyncMock(return_value={"items": []})
        assert (await collect(p.search_emoji(event("鸣潮搜图"))))[0] == main.HELP
        p.request_api.assert_not_called()
        assert (
            "没有找到" in (await collect(p.search_emoji(event("鸣潮搜图 无此角色"))))[0]
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,body,message",
    [
        (401, {"message": "secret-test-token"}, "Token"),
        (403, {}, "拒绝"),
        (404, {}, "没有找到"),
        (429, {}, "频繁"),
        (503, {}, "不可用"),
        (200, {"code": "ERROR", "message": "secret-test-token"}, "异常"),
        (200, [], "异常"),
        (200, "broken json", "异常"),
    ],
)
def test_http_contract_and_safe_errors(monkeypatch, status, body, message):
    async def run():
        received = []

        async def handler(request):
            received.append((dict(request.headers), dict(request.query)))
            if isinstance(body, str):
                return web.Response(text=body, status=status)
            return web.json_response(body, status=status)

        app = web.Application()
        app.router.add_get("/random", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(main, "API_BASE", f"http://127.0.0.1:{port}")
        p = plugin({"api_token": "secret-test-token"})
        try:
            with pytest.raises(main.WuwaAPIError) as error:
                await p.request_api("/random", {"character": "爱弥斯"})
            assert message in str(error.value)
            assert "secret-test-token" not in str(error.value)
            headers, query = received[0]
            assert headers["Authorization"] == "Bearer secret-test-token"
            assert headers["Accept"] == "application/json"
            assert query == {"character": "爱弥斯"}
        finally:
            await p.terminate()
            await runner.cleanup()
        assert p.session.closed

    asyncio.run(run())
