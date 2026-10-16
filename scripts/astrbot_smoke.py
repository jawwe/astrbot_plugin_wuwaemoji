"""Verify actual AstrBot registration, message components and optional live API calls."""

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path


async def run(root: Path, live: bool):
    os.environ["ASTRBOT_ROOT"] = str(root)
    sys.path.insert(0, str(root))
    from astrbot.api.message_components import Image, Plain
    from astrbot.core.message.message_event_result import MessageEventResult
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.star_handler import star_handlers_registry
    from astrbot.core.star.star_manager import PluginManager

    plugin_file = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location("wuwaemoji_smoke", plugin_file)
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
    plugin = module.WuwaEmojiPlugin(None, config)
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.sender = MessageMember(user_id="smoke-test", nickname="smoke-test")
    message.message = [Plain("鸣潮表情帮助")]
    event = AstrMessageEvent(
        "鸣潮表情帮助", message, PlatformMetadata("test", "smoke", "test"), "smoke-test"
    )
    event.is_at_or_wake_command = True
    try:
        commands = {}
        for handler in star_handlers_registry.get_handlers_by_module_name(spec.name):
            for command_filter in handler.event_filters:
                if isinstance(command_filter, CommandFilter):
                    commands[command_filter.command_name] = command_filter
        assert len(commands) == 5, f"Expected 5 commands, got {len(commands)}"
        assert commands["鸣潮表情帮助"].filter(event, {})
        result = [x async for x in plugin.help_command(event)][0]
        assert isinstance(result, MessageEventResult)
        assert any(isinstance(x, Plain) for x in result.chain)
        print("AstrBot import, five registered commands and help message: OK")
        if live:
            data = await plugin.request_api(
                "/emojis",
                {"character": "爱弥斯", "page": 1, "size": 2} | plugin.media_params(),
            )
            assert data["items"] and data["total"] > 0
            print(f"Live search: OK ({data['total']} matching images)")
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
            # Signed media links must work without sending the private API Token.
            async with plugin.session.get(
                result.chain[0].file,
                headers={"Accept": "image/*"},
                allow_redirects=True,
            ) as response:
                assert response.status == 200
                assert response.headers.get("Content-Type", "").startswith("image/")
                image_bytes = await response.read()
                assert image_bytes
                print(
                    f"Live image download without Token: OK ({len(image_bytes)} bytes)"
                )
            print("Actual AstrBot search, select and random image results: OK")
    finally:
        await plugin.terminate()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--astrbot-root", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.astrbot_root.resolve(), args.live))
