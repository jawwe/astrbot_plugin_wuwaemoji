import json
import shlex
import time
from collections import OrderedDict
from urllib.parse import urlsplit

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star

API_BASE = "https://emoji.wuwa.games/apis/api.random-emoji.wuwa.games/v1alpha1"
HELP = """鸣潮表情包搜索
/鸣潮表情 爱弥斯 — 随机一张；不填角色则全库随机
/鸣潮搜图 爱弥斯 — 搜索并展示图片
/鸣潮搜图 角色=爱弥斯 画师=雾雪 页码=2
/鸣潮搜图 关键词=慵懒 — 搜索包标题或资源 ID
/鸣潮搜图 表情包=aimisi-xinjilemu-outfit-1
/鸣潮取图 2 — 重新发送上次搜索的第 2 张（10 分钟内）
/鸣潮角色 爱 — 查角色名称和 slug
/鸣潮表情帮助 — 查看本帮助
筛选值含空格时请加英文引号，例如 画师="Some Artist"。
角色与画师按文章标签筛选，多角色包可能含其他角色。"""


class WuwaAPIError(Exception):
    """Represent a safe, user-facing API failure."""


def parse_query(text: str, *, paginated: bool = False) -> dict:
    """Parse character shorthand and explicit API filters.

    Args:
        text: Command arguments, with optional shell-style quoted values.
        paginated: Whether pagination and keyword search are allowed.

    Returns:
        Validated API query parameters.

    Raises:
        ValueError: If an option or its value is invalid.
    """
    aliases = {
        "角色": "character",
        "画师": "artist",
        "表情包": "pack",
        "关键词": "q",
        "页码": "page",
        "匹配": "match",
    }
    allowed = {"character", "artist", "pack", "match"}
    if paginated:
        allowed |= {"q", "page"}
    params = {}
    positional = []
    try:
        parts = shlex.split(text)
    except ValueError:
        raise ValueError("筛选条件引号未闭合，请检查英文引号。") from None
    for part in parts:
        if "=" not in part:
            positional.append(part)
            continue
        key, value = part.split("=", 1)
        key = aliases.get(key, key)
        if key not in allowed or not value.strip():
            raise ValueError(
                "筛选格式有误，请使用 角色=名称、画师=名称、表情包=ID、关键词=内容、页码=2。"
            )
        if key in params:
            raise ValueError("同一筛选项只能填写一次，多个角色可用英文逗号分隔。")
        params[key] = value.strip()
    if positional:
        if "character" in params:
            raise ValueError("角色不能重复填写。")
        params["character"] = " ".join(positional)
    if "page" in params:
        try:
            page = int(params["page"])
        except ValueError:
            raise ValueError("页码必须是正整数。") from None
        if not 1 <= page <= 100000:
            raise ValueError("页码须在 1 到 100000 之间。")
        params["page"] = page
    if "match" in params and params["match"] not in {"and", "or"}:
        raise ValueError("匹配只能填写 and（同时满足）或 or（满足任一）。")
    return params


def command_args(event: AstrMessageEvent) -> str:
    """Return text after the command name.

    Args:
        event: Current message event.

    Returns:
        Remaining command text, or an empty string.
    """
    parts = event.get_message_str().strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def image_url(item: dict) -> str:
    """Validate a media URL returned by the API.

    Args:
        item: API image record.

    Returns:
        HTTPS media URL.

    Raises:
        WuwaAPIError: If the API record does not contain a valid media URL.
    """
    url = item.get("url")
    if not isinstance(url, str):
        raise WuwaAPIError("表情服务返回的图片地址无效，请稍后重试。")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname
        not in {
            "emoji.wuwa.games",
            "media.wuwa.games",
        }
        or parsed.username
        or parsed.password
    ):
        raise WuwaAPIError("表情服务返回的图片地址无效，请稍后重试。")
    return url


class WuwaEmojiPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.session = None
        self.searches = OrderedDict()

    async def request_api(self, endpoint: str, params: dict) -> dict:
        """Call the API without exposing credentials or upstream error bodies.

        Args:
            endpoint: API resource path.
            params: Query parameters.

        Returns:
            Decoded JSON object.

        Raises:
            WuwaAPIError: For HTTP, network, or response validation failures.
        """
        if self.session is None or self.session.closed:
            timeout = max(3, min(60, int(self.config.get("timeout", 20))))
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                trust_env=True,
            )
        headers = {
            "Accept": "application/json",
            "User-Agent": "astrbot_plugin_wuwaemoji/1.0.0 (AstrBot plugin)",
        }
        token = str(self.config.get("api_token", "") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with self.session.get(
                API_BASE + endpoint,
                params=params,
                headers=headers,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "[WuwaEmoji] API %s HTTP %s", endpoint, response.status
                    )
                    message = {
                        400: "筛选条件无效，请检查角色、画师或表情包名称。",
                        401: "API Token 无效或已过期，请管理员检查插件配置。",
                        403: "API 访问被拒绝，请管理员检查 Token 或服务状态。",
                        404: "没有找到符合条件的表情，请换个角色或筛选条件。",
                        429: "表情 API 调用过于频繁，请稍后再试。",
                    }.get(response.status, "表情服务暂时不可用，请稍后重试。")
                    raise WuwaAPIError(message)
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise WuwaAPIError("表情服务返回的数据过大，请稍后重试。")
                payload = json.loads(body)
                if not isinstance(payload, dict) or "code" in payload:
                    raise WuwaAPIError("表情服务返回异常，请稍后重试。")
                return payload
        except (TimeoutError, aiohttp.ClientError):
            raise WuwaAPIError("连接表情服务超时或失败，请稍后重试。") from None
        except (ValueError, UnicodeError):
            raise WuwaAPIError("表情服务返回的数据格式异常，请稍后重试。") from None

    def media_params(self) -> dict:
        """Return the configured image format.

        Returns:
            API format option.
        """
        return {
            "format": "webp"
            if self.config.get("image_format", "webp") != "original"
            else "original"
        }

    @filter.command("鸣潮表情帮助", alias={"呜哇帮助"})
    async def help_command(self, event: AstrMessageEvent):
        """Show available commands."""
        yield event.plain_result(HELP)

    @filter.command("鸣潮表情", alias={"鸣潮随机表情", "呜哇表情"})
    async def random_emoji(self, event: AstrMessageEvent):
        """Send a random emoji, optionally filtered by character, artist or pack."""
        try:
            params = parse_query(command_args(event)) | self.media_params()
            item = await self.request_api("/random", params)
            yield event.image_result(image_url(item))
        except (ValueError, WuwaAPIError) as exc:
            yield event.plain_result(str(exc))

    @filter.command("鸣潮搜图", alias={"鸣潮表情搜索", "呜哇搜图"})
    async def search_emoji(self, event: AstrMessageEvent):
        """Search a page of images and retain the results for ten minutes."""
        args = command_args(event)
        if not args:
            yield event.plain_result(HELP)
            return
        try:
            params = parse_query(args, paginated=True) | self.media_params()
            size = max(1, min(8, int(self.config.get("page_size", 4))))
            params.update(size=size, page=params.get("page", 1))
            data = await self.request_api("/emojis", params)
            items = data.get("items")
            if not isinstance(items, list) or not all(
                isinstance(x, dict) for x in items
            ):
                raise WuwaAPIError("表情搜索返回的数据格式异常，请稍后重试。")
            if len(items) > size:
                raise WuwaAPIError("表情搜索返回的数据数量异常，请稍后重试。")
            if not items:
                self.searches.pop(
                    (event.unified_msg_origin, event.get_sender_id()), None
                )
                yield event.plain_result(
                    "这一页没有找到表情，请换个角色、关键词或页码。"
                )
                return
            urls = [image_url(item) for item in items]
            now = time.monotonic()
            for key, (deadline, _) in list(self.searches.items()):
                if deadline <= now:
                    del self.searches[key]
            # Scope results to both the conversation and the requesting user.
            key = (event.unified_msg_origin, event.get_sender_id())
            self.searches[key] = (now + 600, urls)
            self.searches.move_to_end(key)
            while len(self.searches) > 128:
                self.searches.popitem(last=False)
            total = data.get("total", "未知")
            chain = [Plain(f"鸣潮表情 · 第 {params['page']} 页 · 共 {total} 张\n")]
            for index, (item, url) in enumerate(zip(items, urls), 1):
                pack = item.get("pack") or {}
                title = (
                    str(pack.get("title", item.get("id", "表情")))
                    if isinstance(pack, dict)
                    else "表情"
                )
                chain.extend([Plain(f"\n{index}. {title}\n"), Image.fromURL(url)])
            tip = "\n发送 /鸣潮取图 序号 可单独取图（10 分钟内）。"
            if data.get("hasNext"):
                next_params = dict(params)
                next_params.pop("size")
                next_params.pop("format")
                next_params["page"] += 1
                tip += "\n下一页：/鸣潮搜图 " + " ".join(
                    shlex.quote(f"{key}={value}") for key, value in next_params.items()
                )
            chain.append(Plain(tip))
            yield event.chain_result(chain)
        except (ValueError, WuwaAPIError) as exc:
            yield event.plain_result(str(exc))

    @filter.command("鸣潮取图", alias={"呜哇取图"})
    async def select_emoji(self, event: AstrMessageEvent):
        """Resend a single image from the caller's last search."""
        key = (event.unified_msg_origin, event.get_sender_id())
        cached = self.searches.get(key)
        if cached is None or cached[0] <= time.monotonic():
            self.searches.pop(key, None)
            yield event.plain_result(
                "搜索记录不存在或已过期，请先使用 /鸣潮搜图 角色名。"
            )
            return
        try:
            index = int(command_args(event))
        except ValueError:
            index = 0
        if not 1 <= index <= len(cached[1]):
            yield event.plain_result(
                f"请填写 1 到 {len(cached[1])} 的序号，例如 /鸣潮取图 1。"
            )
            return
        yield event.image_result(cached[1][index - 1])

    @filter.command("鸣潮角色", alias={"呜哇角色"})
    async def characters(self, event: AstrMessageEvent):
        """Look up character names and slugs using an optional keyword."""
        try:
            data = await self.request_api(
                "/characters",
                {
                    "q": command_args(event),
                    "sort": "count",
                    "order": "desc",
                    "page": 1,
                    "size": 30,
                },
            )
            items = data.get("items")
            if not isinstance(items, list) or not all(
                isinstance(x, dict) for x in items
            ):
                raise WuwaAPIError("角色列表返回的数据格式异常，请稍后重试。")
            lines = [
                f"{x.get('name', '?')} ({x.get('slug', '?')}) · {x.get('count', 0)} 张"
                for x in items
            ]
            text = (
                "鸣潮角色（按表情数量，最多显示 30 项）：\n" + "\n".join(lines)
                if lines
                else "没有找到该角色。"
            )
            yield event.plain_result(text)
        except WuwaAPIError as exc:
            yield event.plain_result(str(exc))

    async def terminate(self):
        """Release the HTTP session and cached signed image URLs."""
        if self.session is not None:
            await self.session.close()
        self.searches.clear()
