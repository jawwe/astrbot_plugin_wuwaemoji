"""Verify actual AstrBot registration, message components and optional live API calls."""

import argparse
import asyncio
import base64
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


async def run(root: Path, live: bool):
    os.environ["ASTRBOT_ROOT"] = str(root)
    sys.path.insert(0, str(root))
    from astrbot.api.message_components import Image, Plain
    from astrbot.core.message.message_event_result import MessageEventResult
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.provider.register import llm_tools
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.star_handler import star_handlers_registry
    from astrbot.core.star.star_manager import PluginManager
    from astrbot.dashboard.services.plugin_page_service import PluginPageService
    from PIL import Image as PILImage

    plugin_file = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location(
        "wuwaemoji_smoke",
        plugin_file,
        submodule_search_locations=[str(plugin_file.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    metadata = PluginManager._load_plugin_metadata(str(plugin_file.parent))
    assert metadata.name == "astrbot_plugin_wuwaemoji"
    assert metadata.version
    config = {}
    if live:
        config = json.loads(
            (root / "data/config/astrbot_plugin_wuwaemoji_config.json").read_text(
                encoding="utf-8"
            )
        )
    registered = []
    context = SimpleNamespace(register_web_api=lambda *args: registered.append(args))
    plugin = module.WuwaEmojiPlugin(
        context, config | {"cache_enabled": True, "enable_llm_tools": True}
    )
    temporary = tempfile.TemporaryDirectory(prefix="wuwaemoji-smoke-")
    plugin.cache = module.EmojiCache(Path(temporary.name))
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.sender = MessageMember(user_id="smoke-test", nickname="smoke-test")
    message.message = [Plain("鸣潮表情帮助")]
    event = AstrMessageEvent(
        "鸣潮表情帮助", message, PlatformMetadata("test", "smoke", "test"), "smoke-test"
    )
    event.is_at_or_wake_command = True
    try:
        assert len(registered) == 11
        assert all(
            route[0].startswith("/astrbot_plugin_wuwaemoji/admin/")
            for route in registered
        )
        metadata.root_dir_name = plugin_file.parent.name
        page_service = PluginPageService(
            SimpleNamespace(plugin_store_path=str(plugin_file.parent.parent))
        )
        views = await page_service.discover_plugin_pages(metadata)
        assert [view.name for view in views] == ["admin"]
        assert metadata.i18n["zh-CN"]["views"]["admin"]["title"] == "表情图库"
        print(
            "Actual AstrBot view discovery, localized title and eleven registered admin APIs: OK"
        )
        commands = {}
        for handler in star_handlers_registry.get_handlers_by_module_name(spec.name):
            for command_filter in handler.event_filters:
                if isinstance(command_filter, CommandFilter):
                    commands[command_filter.command_name] = command_filter
        assert len(commands) == 7, f"Expected 7 commands, got {len(commands)}"
        assert commands["鸣潮表情帮助"].filter(event, {})
        result = [x async for x in plugin.help_command(event)][0]
        assert isinstance(result, MessageEventResult)
        assert any(isinstance(x, Plain) for x in result.chain)
        print("AstrBot import, seven registered commands and help message: OK")
        for name, expected in {
            "list_wuwa_emoji_categories": set(),
            "search_wuwa_emojis": {"query", "category", "character"},
            "send_wuwa_emoji": {"emoji_id", "category", "character"},
        }.items():
            tool = llm_tools.get_func(name)
            assert tool is not None
            assert set(tool.parameters.get("properties", {})) == expected
        buffer = io.BytesIO()
        PILImage.new("RGB", (8, 8), "blue").save(buffer, format="PNG")
        cached = await plugin.cache.put(
            {"id": "smoke", "characters": [{"name": "爱弥斯", "slug": "aimisi"}]},
            buffer.getvalue(),
            "开心",
        )
        event.send = AsyncMock()
        send_tool = llm_tools.get_func("send_wuwa_emoji")
        result = await send_tool.handler(
            plugin, event, emoji_id=cached["id"], category="开心", character="爱弥斯"
        )
        assert "已向当前聊天" in result
        sent = event.send.call_args.args[0]
        assert isinstance(sent, MessageEventResult) and isinstance(sent.chain[0], Image)
        req = module.ProviderRequest(system_prompt="unchanged")
        await plugin.category_hints(event, req)
        assert req.system_prompt == "unchanged" and req.extra_user_content_parts
        await plugin.cache.clear("全部")
        print(
            "Three actual AstrBot tool schemas, callable send handler and category context: OK (simulated transport)"
        )
        if live:
            for kind in ("characters", "artists"):
                directory = await plugin.request_api(
                    "/" + kind, {"page": 1, "size": 100}
                )
                assert directory["items"] and all(
                    "name" in item and "slug" in item for item in directory["items"]
                )
                print(
                    f"Live {kind} directory: OK ({directory.get('total', len(directory['items']))} entries)"
                )
            packs = await plugin.request_api(
                "/packs", {"character": "爱弥斯", "page": 1, "size": 1}
            )
            assert packs["items"] and packs["items"][0]["id"]
            packed = await plugin.request_api(
                "/emojis",
                {"pack": packs["items"][0]["id"], "page": 1, "size": 1}
                | plugin.media_params(),
            )
            assert packed["items"]
            print("Live selected-category pack listing and pack image filter: OK")
            data = await plugin.request_api(
                "/emojis",
                {"character": "爱弥斯", "page": 1, "size": 2} | plugin.media_params(),
            )
            assert data["items"] and data["total"] > 0
            print(f"Live search: OK ({data['total']} matching images)")
            animated = next(
                (item for item in data["items"] if item.get("animated")), None
            )
            if animated:
                original = await plugin.download_image(animated)
                preview = plugin.admin.thumbnail(original)
                assert (
                    preview["animated"]
                    and base64.b64decode(preview["data_url"].split(",", 1)[1])
                    == original
                )
                with PILImage.open(io.BytesIO(original)) as image:
                    assert image.n_frames > 1
                    print(
                        f"Live native animated preview: OK ({image.n_frames} frames, {image.format})"
                    )
            event.message_str = "鸣潮搜图 爱弥斯"
            assert commands["鸣潮搜图"].filter(event, {})
            search = [x async for x in plugin.search_emoji(event)][0]
            assert isinstance(search, MessageEventResult)
            assert any(isinstance(x, Image) for x in search.chain)
            event.message_str = "鸣潮取图 1"
            selected = [x async for x in plugin.select_emoji(event)][0]
            assert isinstance(selected.chain[0], Image)
            event.message_str = "鸣潮表情 爱弥斯"
            assert commands["鸣潮表情"].filter(event, {})
            result = [x async for x in plugin.random_emoji(event)][0]
            assert isinstance(result.chain[0], Image)
            image_path = Path(await result.chain[0].convert_to_file_path())
            assert image_path.is_file() and image_path.is_relative_to(
                Path(temporary.name)
            )
            print(
                f"Live download and persistent local image result: OK ({image_path.stat().st_size} bytes)"
            )
            catalog = await plugin.cache.catalog()
            assert catalog["count"] > 0
            assert any(
                x["name"].startswith("角色：") and x["count"]
                for x in catalog["categories"]
            )
            print("Actual AstrBot search, select and random image results: OK")
    finally:
        await plugin.terminate()
        temporary.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--astrbot-root", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.astrbot_root.resolve(), args.live))
