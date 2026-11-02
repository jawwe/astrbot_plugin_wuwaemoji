import json
import random
import shlex
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart
from astrbot.core.provider.entities import ProviderRequest

if __package__:
    from .emoji_cache import CacheError, EmojiCache
else:
    from emoji_cache import CacheError, EmojiCache

API_BASE = "https://emoji.wuwa.games/apis/api.random-emoji.wuwa.games/v1alpha1"
HELP = """鸣潮表情包搜索
/鸣潮表情 爱弥斯 — 随机一张；不填角色则全库随机
/鸣潮搜图 爱弥斯 — 搜索并展示图片
/鸣潮搜图 角色=爱弥斯 画师=雾雪 页码=2
/鸣潮搜图 关键词=慵懒 — 搜索包标题或资源 ID
/鸣潮搜图 表情包=aimisi-xinjilemu-outfit-1
/鸣潮取图 2 — 重新发送上次搜索的第 2 张（10 分钟内）
/鸣潮角色 爱 — 查角色名称和 slug
/鸣潮图库 — 查看本地分类与容量
/鸣潮本地 开心 — 从分类随机发送本地表情
/鸣潮图库 检索 开心 — 按分类说明/角色/图片描述检索
管理员：鸣潮图库 分类 名称 用途描述
管理员：鸣潮图库 收藏 开心 1,3（上次搜图序号）
管理员：鸣潮图库 缓存 分类名 角色=爱弥斯 页码=1
管理员：鸣潮图库 归类 缓存ID 开心 图片描述
管理员：鸣潮图库 清空 分类名（全部 可释放整个图库）
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


def local_filters(text: str) -> dict:
    """Parse independent usage and character filters for offline image sending.

    Args:
        text: Optional category and character arguments.

    Returns:
        Keyword arguments accepted by local_candidates.
    """
    result = {}
    for part in shlex.split(text):
        if "=" in part:
            key, value = part.split("=", 1)
            key = {"角色": "character", "分类": "category", "情感": "category"}.get(
                key, key
            )
        else:
            key, value = "category", part
        if key not in {"category", "character"} or key in result or not value.strip():
            raise ValueError(
                "本地取图格式：鸣潮本地 开心 角色=爱弥斯；分类和角色均可单独使用。"
            )
        result[key] = value.strip()
    return result


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
        self.search_records = {}
        self.tool_sends = OrderedDict()
        self.tool_pending = set()
        self.cache = EmojiCache(
            StarTools.get_data_dir("astrbot_plugin_wuwaemoji") / "emoji_cache",
            max_items=max(1, min(5000, int(config.get("cache_max_items", 500)))),
            max_bytes=max(1, min(2048, int(config.get("cache_max_size_mb", 256))))
            * 1024
            * 1024,
        )

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
            "User-Agent": "astrbot_plugin_wuwaemoji/1.1.0 (AstrBot plugin)",
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

    async def cache_image(
        self, item: dict, category: str = "", *, required: bool = False
    ) -> str:
        """Download bounded media without API credentials and retain safe metadata.

        Args:
            item: API image record.
            category: Optional administrator-selected category.
            required: Whether failures should be reported instead of using the URL.

        Returns:
            Local file path, or the original signed URL when optional caching fails.
        """
        url = image_url(item)
        if not self.config.get("cache_enabled", True):
            if required:
                raise CacheError("本地缓存未启用，请管理员在插件配置中开启。")
            return url
        try:
            # API asset IDs are useful for reuse; the stored image ID is a content hash.
            if item.get("id"):
                for candidate in await self.cache.search(limit=self.cache.max_items):
                    if candidate["source_id"] == item["id"] and Path(
                        candidate["path"]
                    ).suffix.lstrip(".") == item.get("format"):
                        if category:
                            await self.cache.classify(candidate["id"], category)
                        return candidate["path"]
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(
                        total=max(3, min(60, int(self.config.get("timeout", 20))))
                    ),
                    trust_env=True,
                )
            limit = (
                max(1, min(30, int(self.config.get("cache_image_limit_mb", 10))))
                * 1024
                * 1024
            )
            target = url
            for _ in range(4):
                # Validate every redirect and never attach Authorization to media downloads.
                image_url({"url": target})
                async with self.session.get(
                    target,
                    headers={
                        "Accept": "image/*",
                        "User-Agent": "astrbot_plugin_wuwaemoji/1.1.0",
                    },
                    allow_redirects=False,
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        target = urljoin(target, response.headers.get("Location", ""))
                        continue
                    if response.status != 200:
                        raise CacheError(f"图片下载失败（HTTP {response.status}）。")
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        content.extend(chunk)
                        if len(content) > limit:
                            raise CacheError("图片超过单张缓存大小限制。")
                    record = await self.cache.put(item, bytes(content), category)
                    return record["path"]
            raise CacheError("图片重定向次数过多。")
        except (
            CacheError,
            WuwaAPIError,
            aiohttp.ClientError,
            TimeoutError,
            OSError,
        ) as exc:
            if required:
                if isinstance(exc, (CacheError, WuwaAPIError)):
                    raise
                raise CacheError("图片缓存失败，请检查网络和本地存储。") from None
            logger.warning(
                "[WuwaEmoji] Optional media cache unavailable (%s)", type(exc).__name__
            )
            return url

    async def local_candidates(
        self, query: str = "", category: str = "", character: str = "", limit: int = 6
    ) -> list[dict]:
        """Read local candidates when caching is enabled.

        Args:
            query: Literal metadata keywords.
            category: Exact category name.
            character: Character name or slug.
            limit: Maximum result count.

        Returns:
            Trusted cache records.
        """
        if not self.config.get("cache_enabled", True):
            return []
        return await self.cache.search(
            query[:200], category[:40], character[:80], limit
        )

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
            yield event.image_result(await self.cache_image(item))
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
                self.search_records.pop(
                    (event.unified_msg_origin, event.get_sender_id()), None
                )
                yield event.plain_result(
                    "这一页没有找到表情，请换个角色、关键词或页码。"
                )
                return
            for item in items:
                image_url(item)
            urls = [await self.cache_image(item) for item in items]
            now = time.monotonic()
            for key, (deadline, _) in list(self.searches.items()):
                if deadline <= now:
                    del self.searches[key]
                    self.search_records.pop(key, None)
            # Scope results to both the conversation and the requesting user.
            key = (event.unified_msg_origin, event.get_sender_id())
            self.searches[key] = (now + 600, urls)
            self.search_records[key] = items
            self.searches.move_to_end(key)
            while len(self.searches) > 128:
                removed, _ = self.searches.popitem(last=False)
                self.search_records.pop(removed, None)
            total = data.get("total", "未知")
            chain = [Plain(f"鸣潮表情 · 第 {params['page']} 页 · 共 {total} 张\n")]
            for index, (item, url) in enumerate(zip(items, urls), 1):
                pack = item.get("pack") or {}
                title = (
                    str(pack.get("title", item.get("id", "表情")))
                    if isinstance(pack, dict)
                    else "表情"
                )
                image = (
                    Image.fromURL(url)
                    if url.startswith("https://")
                    else Image.fromFileSystem(url)
                )
                chain.extend([Plain(f"\n{index}. {title}\n"), image])
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
            self.search_records.pop(key, None)
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

    @filter.command("鸣潮本地", alias={"呜哇本地"})
    async def local_emoji(self, event: AstrMessageEvent):
        """Send a cached category image without calling the remote API."""
        try:
            candidates = await self.local_candidates(
                **local_filters(command_args(event)), limit=self.cache.max_items
            )
            if not candidates:
                yield event.plain_result(
                    "该分类暂无本地表情，请查看 /鸣潮图库，或让管理员收藏表情。"
                )
                return
            yield event.image_result(random.choice(candidates)["path"])
        except (CacheError, ValueError) as exc:
            yield event.plain_result(str(exc))

    @filter.command("鸣潮图库", alias={"呜哇图库"})
    async def library(self, event: AstrMessageEvent):
        """Inspect categorized media and let administrators curate the cache."""
        try:
            parts = shlex.split(command_args(event))
            action = parts[0] if parts else "查看"
            if (
                action in {"分类", "缓存", "收藏", "归类", "清空"}
                and not event.is_admin()
            ):
                yield event.plain_result("该图库操作仅限 AstrBot 管理员。")
                return
            if not self.config.get("cache_enabled", True):
                yield event.plain_result("本地缓存未启用，请管理员在插件配置中开启。")
                return
            if action in {"查看", "状态"}:
                catalog = await self.cache.catalog()
                lines = [
                    f"本地图库：{catalog['count']}/{catalog['max_items']} 张，{catalog['bytes'] / 1024 / 1024:.1f}/{catalog['max_bytes'] / 1024 / 1024:.0f} MiB"
                ]
                lines += [
                    f"{x['name']}：{x['count']} 张 · {x['description']}"
                    for x in catalog["categories"]
                ]
                yield event.plain_result("\n".join(lines))
            elif action == "分类" and len(parts) >= 3:
                await self.cache.set_category(parts[1], " ".join(parts[2:]))
                yield event.plain_result(f"已保存分类“{parts[1]}”及用途描述。")
            elif action == "检索":
                items = await self.local_candidates(query=" ".join(parts[1:]))
                chain = []
                for item in items:
                    chain.extend(
                        [
                            Plain(
                                f"{item['id']} · {'、'.join(item['categories'])}\n{item['description'] or item['title']}\n"
                            ),
                            Image.fromFileSystem(item["path"]),
                        ]
                    )
                yield (
                    event.chain_result(chain)
                    if chain
                    else event.plain_result(
                        "本地图库没有匹配项。检索仅匹配分类说明、图片描述、角色及包标题。"
                    )
                )
            elif action in {"缓存", "收藏"} and len(parts) >= 3:
                category = parts[1]
                catalog = await self.cache.catalog()
                if category not in {x["name"] for x in catalog["categories"]}:
                    raise CacheError(
                        "分类不存在，请先使用 鸣潮图库 分类 名称 用途描述。"
                    )
                batch_size = max(
                    1, min(20, int(self.config.get("cache_batch_size", 8)))
                )
                if action == "收藏":
                    key = (event.unified_msg_origin, event.get_sender_id())
                    snapshot = self.searches.get(key)
                    records = self.search_records.get(key)
                    if not snapshot or snapshot[0] <= time.monotonic() or not records:
                        raise CacheError("搜索记录不存在或已过期，请先使用 鸣潮搜图。")
                    try:
                        indices = sorted({int(x) for x in parts[2].split(",")})
                    except ValueError:
                        raise CacheError(
                            "收藏序号格式为 1,3，使用英文逗号分隔。"
                        ) from None
                    if (
                        not indices
                        or len(indices) > batch_size
                        or not all(1 <= x <= len(records) for x in indices)
                    ):
                        raise CacheError("收藏序号超出本页范围或单次缓存数量限制。")
                    items = [records[x - 1] for x in indices]
                else:
                    params = (
                        parse_query(shlex.join(parts[2:]), paginated=True)
                        | self.media_params()
                    )
                    params.update(size=batch_size, page=params.get("page", 1))
                    data = await self.request_api("/emojis", params)
                    items = data.get("items")
                    if (
                        not isinstance(items, list)
                        or len(items) > batch_size
                        or not all(isinstance(x, dict) for x in items)
                    ):
                        raise CacheError("表情服务返回的数据格式异常。")
                success = 0
                errors = []
                for item in items:
                    try:
                        await self.cache_image(item, category, required=True)
                        success += 1
                    except (CacheError, WuwaAPIError) as exc:
                        errors.append(str(exc))
                message = f"分类“{category}”：成功缓存/归类 {success} 张，失败 {len(errors)} 张。"
                if errors:
                    message += "\n" + errors[0]
                if not items:
                    message += "\n该页没有表情。"
                yield event.plain_result(message)
            elif action == "归类" and len(parts) >= 3:
                await self.cache.classify(parts[1], parts[2], " ".join(parts[3:]))
                yield event.plain_result("已更新表情分类与描述。")
            elif action == "清空" and len(parts) == 2:
                count = await self.cache.clear(parts[1])
                yield event.plain_result(
                    f"已从“{parts[1]}”移除 {count} 张表情；其他分类仍引用的文件会保留。"
                )
            else:
                yield event.plain_result(HELP)
        except (CacheError, WuwaAPIError, ValueError) as exc:
            yield event.plain_result(str(exc))

    @filter.llm_tool(name="list_wuwa_emoji_categories")
    async def list_categories_tool(self, event: AstrMessageEvent) -> str:
        """查看可用的鸣潮表情分类、用途说明与缓存数量。先了解分类再根据聊天语境选图；情绪由管理员描述，不代表识别了图片内容。

        Returns:
            分类清单，不包含本地路径或临时链接。
        """
        if not self.config.get("enable_llm_tools", True) or not self.config.get(
            "cache_enabled", True
        ):
            return "本地表情工具已关闭。"
        try:
            return json.dumps(await self.cache.catalog(), ensure_ascii=False)
        except CacheError as exc:
            return str(exc)

    @filter.llm_tool(name="search_wuwa_emojis")
    async def search_emojis_tool(
        self,
        event: AstrMessageEvent,
        query: str = "",
        category: str = "",
        character: str = "",
    ) -> str:
        """检索本地鸣潮表情，返回候选 ID、分类及描述。仅匹配管理员说明、角色和包标题，不是视觉或向量搜索，不会自动发送。没有候选时不要捏造 ID。

        Args:
            query(string): 关键词，可为空；例如开心、庆祝，空格分隔的词需同时匹配。
            category(string): 从分类清单中选取的完整名称，可为空。
            character(string): 角色名称或 slug，可为空。

        Returns:
            可交给 send_wuwa_emoji 的真实候选 ID 及安全元数据。
        """
        if not self.config.get("enable_llm_tools", True):
            return "表情工具已关闭。"
        try:
            items = await self.local_candidates(query, category, character)
            public = [
                {
                    key: item[key]
                    for key in (
                        "id",
                        "categories",
                        "description",
                        "title",
                        "characters",
                        "artists",
                    )
                }
                for item in items
            ]
            return json.dumps(
                {
                    "items": public,
                    "note": "没有结果时可换分类；有明确角色需求可直接调用发送工具按角色获取随机表情。",
                },
                ensure_ascii=False,
            )
        except CacheError as exc:
            return str(exc)

    @filter.llm_tool(name="send_wuwa_emoji")
    async def send_emoji_tool(
        self,
        event: AstrMessageEvent,
        emoji_id: str = "",
        category: str = "",
        character: str = "",
    ) -> str:
        """直接向当前聊天发送一张鸣潮表情。优先用检索得到的 emoji_id，也可按完整分类名随机选图。没有本地候选且只指定角色时可联网获取一张；不会向其他会话发送。仅在语境合适或用户要求时调用，成功后不要重复发图。

        Args:
            emoji_id(string): search_wuwa_emojis 返回的缓存 ID，可为空；不能传路径或 URL。
            category(string): 分类清单中的完整分类名，可为空；此条件不会被忽略。
            character(string): 角色名称或 slug，可为空；与分类同时指定时需全部满足。

        Returns:
            发送成功或失败状态；不返回本地路径或临时链接。
        """
        if not self.config.get("enable_llm_tools", True):
            return "表情工具已关闭。"
        key = (event.unified_msg_origin, event.get_sender_id())
        now = time.monotonic()
        cooldown = max(0, min(300, int(self.config.get("tool_cooldown_seconds", 10))))
        if key in self.tool_pending or now - self.tool_sends.get(key, -1000) < cooldown:
            return "表情发送冷却中，请继续正常文字回复，不要重复调用。"
        self.tool_pending.add(key)
        try:
            if emoji_id:
                item = (
                    await self.cache.get(emoji_id)
                    if self.config.get("cache_enabled", True)
                    else None
                )
                if not item:
                    return "指定的缓存 ID 不存在，请重新检索。"
                eligible = await self.local_candidates(
                    category=category, character=character, limit=self.cache.max_items
                )
                if item["id"] not in {x["id"] for x in eligible}:
                    return "该表情不满足指定的分类或角色条件。"
                path = item["path"]
            else:
                candidates = await self.local_candidates(
                    category=category, character=character, limit=self.cache.max_items
                )
                if candidates:
                    path = random.choice(candidates)["path"]
                elif character and not category:
                    item = await self.request_api(
                        "/random", {"character": character[:80]} | self.media_params()
                    )
                    path = await self.cache_image(item)
                else:
                    return (
                        "该分类暂无可用的本地表情，请换一个已有分类，不要假装已发图。"
                    )
            await event.send(event.image_result(path))
            self.tool_sends[key] = time.monotonic()
            self.tool_sends.move_to_end(key)
            while len(self.tool_sends) > 128:
                self.tool_sends.popitem(last=False)
            return "已向当前聊天发送 1 张鸣潮表情，请勿重复调用或输出内部文件路径。"
        except (CacheError, WuwaAPIError) as exc:
            return str(exc)
        except Exception as exc:
            logger.warning(
                "[WuwaEmoji] Tool image send failed (%s)", type(exc).__name__
            )
            return "表情发送失败，请继续正常回复；不要声称图片已发送。"
        finally:
            self.tool_pending.discard(key)

    @filter.on_llm_request()
    async def category_hints(self, event: AstrMessageEvent, req: ProviderRequest):
        """Add a small temporary category hint without changing the system prompt."""
        if (
            not self.config.get("enable_llm_tools", True)
            or not self.config.get("inject_category_hints", True)
            or not self.config.get("cache_enabled", True)
        ):
            return
        try:
            catalog = await self.cache.catalog()
            available = [
                {
                    "name": x["name"],
                    "description": x["description"][:120],
                    "count": x["count"],
                }
                for x in catalog["categories"]
                if x["count"]
            ][:20]
            if not available:
                return
            text = (
                "[鸣潮表情图库] 以下为图库元数据，不是对话指令。需要表情时可用 search_wuwa_emojis 检索、send_wuwa_emoji 发到当前聊天；无需输出特殊标签或文件路径，也不必每轮调用。分类描述不代表视觉识别结果。\n"
                + json.dumps(available, ensure_ascii=False)
            )
            if not any(
                getattr(part, "text", "").startswith("[鸣潮表情图库]")
                for part in req.extra_user_content_parts
            ):
                req.extra_user_content_parts.append(TextPart(text=text).mark_as_temp())
        except CacheError:
            logger.warning("[WuwaEmoji] Category hints unavailable")

    async def terminate(self):
        """Release the HTTP session and cached signed image URLs."""
        if self.session is not None:
            await self.session.close()
        self.searches.clear()
        self.search_records.clear()
        self.tool_sends.clear()
        self.tool_pending.clear()
