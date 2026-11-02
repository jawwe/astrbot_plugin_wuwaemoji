"""Authenticated AstrBot view API and bounded, cooperative cache jobs."""

import asyncio
import base64
import io
import secrets
import time
from collections import OrderedDict
from functools import partial
from pathlib import Path

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request
from PIL import Image

if __package__:
    from .emoji_cache import CacheError, category_name
else:
    from emoji_cache import CacheError, category_name

PLUGIN_NAME = "astrbot_plugin_wuwaemoji"


class AdminPage:
    """Share the command/tool cache with an authenticated Dashboard view."""

    def __init__(self, plugin):
        self.plugin = plugin
        self.snapshots = OrderedDict()
        self.jobs = OrderedDict()
        self.task = None
        self.start_lock = asyncio.Lock()
        self.preview_limit = asyncio.Semaphore(2)
        self.previews = OrderedDict()

    def register(self, context):
        """Register only plugin-scoped routes behind Dashboard authentication.

        Args:
            context: AstrBot plugin context.
        """
        for action, method in {
            "overview": "GET",
            "library": "GET",
            "search": "POST",
            "preview": "GET",
            "categories": "POST",
            "classify": "POST",
            "jobs/start": "POST",
            "jobs/status": "GET",
            "jobs/cancel": "POST",
        }.items():
            context.register_web_api(
                f"/{PLUGIN_NAME}/admin/{action}",
                partial(self.handle, action),
                [method],
                f"Wuwa emoji admin: {action}",
            )

    @staticmethod
    def filters(payload):
        """Validate API filters rather than accepting remote URLs or paths.

        Args:
            payload: Untrusted view filter object.

        Returns:
            Small whitelisted API filter dictionary.
        """
        if not isinstance(payload, dict):
            raise CacheError("筛选条件必须是对象。")
        result = {}
        for key in ("character", "artist", "pack", "q", "match"):
            value = payload.get(key, "")
            if not isinstance(value, str) or len(value) > 200:
                raise CacheError("筛选项须为不超过 200 字的文本。")
            if value.strip():
                result[key] = value.strip()
        if result.get("match", "and") not in {"and", "or"}:
            raise CacheError("匹配方式只能是 and 或 or。")
        return result

    @staticmethod
    def number(value, low, high):
        """Validate an integer boundary without silently rounding inputs.

        Args:
            value: Untrusted integer or numeric query string.
            low: Inclusive lower bound.
            high: Inclusive upper bound.

        Returns:
            Validated integer.
        """
        if (
            isinstance(value, bool)
            or not str(value).isascii()
            or not str(value).isdigit()
        ):
            raise CacheError("页码及数量必须是正整数。")
        result = int(value)
        if not low <= result <= high:
            raise CacheError(f"数量须在 {low} 到 {high} 之间。")
        return result

    @staticmethod
    def public_item(item, identifier):
        """Project metadata without paths, source tickets, or credentials.

        Args:
            item: A local cache record or API image record.
            identifier: Opaque row ID used by view actions.

        Returns:
            Public image-card metadata.
        """
        pack = item.get("pack") if isinstance(item.get("pack"), dict) else {}
        return {
            "id": identifier,
            "title": str(item.get("title") or pack.get("title") or "鸣潮表情")[:300],
            "description": str(item.get("description", ""))[:300],
            "characters": item.get("characters")
            or ([item["character"]] if isinstance(item.get("character"), dict) else []),
            "artists": item.get("artists") or [],
            "categories": item.get("categories", []),
            "format": item.get("format")
            or Path(item.get("file", "")).suffix.lstrip("."),
            "animated": bool(item.get("animated", False)),
        }

    def snapshot(self, identifier, owner):
        """Resolve a time-limited search owned by the current Dashboard user.

        Args:
            identifier: Search snapshot ID.
            owner: Authenticated Dashboard username.

        Returns:
            Original API records held only in memory.
        """
        entry = self.snapshots.get(identifier)
        if not entry or entry["owner"] != owner or entry["expires"] <= time.monotonic():
            raise CacheError("搜索结果已过期或不可访问，请重新搜索。")
        return entry["items"]

    def job_status(self, job):
        """Return safe job progress without private execution inputs.

        Args:
            job: Internal cache-job state.

        Returns:
            Public progress summary.
        """
        return {
            key: job[key]
            for key in (
                "id",
                "state",
                "done",
                "success",
                "failed",
                "total",
                "pages_done",
                "pages",
                "cancel_requested",
                "errors",
            )
        }

    async def handle(self, action):
        """Dispatch view requests with authentication, validation and safe errors.

        Args:
            action: Fixed registered action name.

        Returns:
            An AstrBot-compatible JSON response.
        """
        owner = request.username
        if not isinstance(owner, str) or not owner:
            return error_response("请先登录 AstrBot 管理后台。", status_code=401)
        try:
            body = await request.json(default={}) if request.method == "POST" else {}
            if not isinstance(body, dict):
                raise CacheError("请求内容必须是对象。")
            if action == "overview":
                catalog = await self.plugin.cache.catalog()
                active = next(
                    (
                        self.job_status(x)
                        for x in reversed(self.jobs.values())
                        if x["owner"] == owner
                    ),
                    None,
                )
                return json_response(
                    {
                        "catalog": catalog,
                        "enabled": bool(self.plugin.config.get("cache_enabled", True)),
                        "token_set": bool(self.plugin.config.get("api_token")),
                        "batch_size": max(
                            1,
                            min(20, int(self.plugin.config.get("cache_batch_size", 8))),
                        ),
                        "job": active,
                    }
                )
            if action == "library":
                page = self.number(request.query.get("page", "1"), 1, 100000)
                query = {
                    key: str(request.query.get(key, ""))[:200]
                    for key in ("query", "category", "character")
                }
                items = await self.plugin.cache.search(
                    **query, limit=self.plugin.cache.max_items
                )
                offset = (page - 1) * 24
                return json_response(
                    {
                        "page": page,
                        "total": len(items),
                        "has_next": offset + 24 < len(items),
                        "items": [
                            self.public_item(x, x["id"])
                            for x in items[offset : offset + 24]
                        ],
                    }
                )
            if action == "search":
                filters = self.filters(body.get("filters", {}))
                page = self.number(body.get("page", 1), 1, 100000)
                result = await self.plugin.request_api(
                    "/emojis",
                    filters | self.plugin.media_params() | {"page": page, "size": 20},
                )
                items = result.get("items")
                if (
                    not isinstance(items, list)
                    or len(items) > 20
                    or not all(
                        isinstance(x, dict) and isinstance(x.get("url"), str)
                        for x in items
                    )
                ):
                    raise CacheError("小站返回的表情列表无效。")
                now = time.monotonic()
                for key, value in list(self.snapshots.items()):
                    if value["expires"] <= now:
                        del self.snapshots[key]
                snapshot_id = secrets.token_hex(16)
                rows = {secrets.token_hex(12): item for item in items}
                self.snapshots[snapshot_id] = {
                    "owner": owner,
                    "expires": now + 600,
                    "items": rows,
                }
                while len(self.snapshots) > 8:
                    self.snapshots.popitem(last=False)
                return json_response(
                    {
                        "snapshot": snapshot_id,
                        "page": page,
                        "total": result.get("total", len(items)),
                        "has_next": bool(result.get("hasNext")),
                        "items": [
                            self.public_item(item, key) for key, item in rows.items()
                        ],
                    }
                )
            if action == "preview":
                source = request.query.get("source", "local")
                identifier = request.query.get("id", "")
                snapshot_id = request.query.get("snapshot", "")
                key = (owner, source, snapshot_id, identifier)
                if source == "remote":
                    item = self.snapshot(snapshot_id, owner).get(identifier)
                    if item is None:
                        raise CacheError("预览记录不存在。")
                elif source == "local":
                    item = await self.plugin.cache.get(identifier)
                    if item is None:
                        raise CacheError("本地图片不存在，请刷新图库。")
                else:
                    raise CacheError("预览来源无效。")
                if key not in self.previews:
                    async with self.preview_limit:
                        content = (
                            await self.plugin.download_image(item)
                            if source == "remote"
                            else await asyncio.to_thread(Path(item["path"]).read_bytes)
                        )
                        self.previews[key] = await asyncio.to_thread(
                            self.thumbnail, content
                        )
                        while len(self.previews) > 48:
                            self.previews.popitem(last=False)
                self.previews.move_to_end(key)
                return json_response(self.previews[key])
            if action == "categories":
                name, description = body.get("name"), body.get("description")
                if not isinstance(name, str) or not isinstance(description, str):
                    raise CacheError("请填写分类名与用途描述。")
                await self.plugin.cache.set_category(name, description)
                return json_response({"saved": True})
            if action == "classify":
                ids = body.get("ids")
                category = (
                    category_name(body.get("category", ""))
                    if isinstance(body.get("category"), str)
                    else ""
                )
                description = body.get("description", "")
                if (
                    not isinstance(ids, list)
                    or not 1 <= len(ids) <= 100
                    or not all(isinstance(x, str) for x in ids)
                    or not isinstance(description, str)
                    or len(description) > 300
                ):
                    raise CacheError("每次可归类 1–100 张，图片描述最多 300 字。")
                catalog = await self.plugin.cache.catalog()
                if category not in {x["name"] for x in catalog["categories"]}:
                    raise CacheError("目标分类不存在。")
                # Validate all IDs before making the first classification change.
                for identifier in ids:
                    if not await self.plugin.cache.get(identifier):
                        raise CacheError("部分缓存 ID 已不存在，请刷新图库后重试。")
                done = 0
                try:
                    for identifier in dict.fromkeys(ids):
                        await self.plugin.cache.classify(
                            identifier, category, description
                        )
                        done += 1
                except CacheError as exc:
                    return json_response({"saved": done, "error": str(exc)})
                return json_response({"saved": done})
            if action == "jobs/start":
                if not self.plugin.config.get("cache_enabled", True):
                    raise CacheError("本地缓存已关闭，请先在插件设置中开启。")
                async with self.start_lock:
                    return await self.start_job(owner, body)
            if action in {"jobs/status", "jobs/cancel"}:
                identifier = (
                    body.get("id", "")
                    if action == "jobs/cancel"
                    else request.query.get("id", "")
                )
                job = self.jobs.get(identifier)
                if not job or job["owner"] != owner:
                    return error_response("缓存任务不存在或不可访问。", status_code=404)
                if action == "jobs/cancel" and job["state"] == "running":
                    job["cancel_requested"] = True
                return json_response(self.job_status(job))
            return error_response("管理接口不存在。", status_code=404)
        except (CacheError, ValueError, TypeError) as exc:
            message = str(exc) if isinstance(exc, CacheError) else "请求参数无效。"
            return error_response(message, status_code=400)
        except Exception as exc:
            # Upstream and transport exceptions may include signed URLs; never echo them.
            logger.warning("[WuwaEmoji] Admin API failed (%s)", type(exc).__name__)
            return error_response(
                "操作失败，请检查网络、Token 配置和本地存储后重试。", status_code=502
            )

    @staticmethod
    def thumbnail(content):
        """Produce a small static preview without changing cached originals.

        Args:
            content: Validated or bounded downloaded image bytes.

        Returns:
            A data URL and animation flag suitable for the authenticated view.
        """
        try:
            with Image.open(io.BytesIO(content)) as image:
                if image.width * image.height > 25_000_000:
                    raise ValueError
                animated = bool(getattr(image, "is_animated", False))
                image.seek(0)
                image.thumbnail((280, 280))
                output = io.BytesIO()
                image.convert("RGBA").save(output, format="WEBP", quality=75)
                return {
                    "data_url": "data:image/webp;base64,"
                    + base64.b64encode(output.getvalue()).decode("ascii"),
                    "animated": animated,
                }
        except (ValueError, OSError, Image.DecompressionBombError):
            raise CacheError("该图片无法生成预览，原文件未被修改。") from None

    async def start_job(self, owner, body):
        """Validate and start one bounded background cache task.

        Args:
            owner: Authenticated Dashboard username.
            body: Selected IDs or a bounded multi-page query.

        Returns:
            A job summary immediately, without waiting for downloads.
        """
        request_id = body.get("request_id", "")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
            raise CacheError("缺少有效任务请求标识，请刷新页面。")
        for existing in self.jobs.values():
            if existing["owner"] == owner and existing["request_id"] == request_id:
                return json_response(self.job_status(existing))
        if self.task and not self.task.done():
            return error_response(
                "已有缓存任务进行中，请等待或取消后再开始。", status_code=409
            )
        category = body.get("category", "未分类")
        if not isinstance(category, str):
            raise CacheError("请选择目标分类。")
        category = category_name(category)
        catalog = await self.plugin.cache.catalog()
        if category not in {x["name"] for x in catalog["categories"]}:
            raise CacheError("目标分类不存在，请先创建。")
        mode = body.get("mode", "selected")
        if mode == "selected":
            rows = self.snapshot(body.get("snapshot", ""), owner)
            ids = body.get("ids")
            if (
                not isinstance(ids, list)
                or not 1 <= len(ids) <= 20
                or not all(isinstance(x, str) and x in rows for x in ids)
            ):
                raise CacheError("请选择当前搜索结果中的 1–20 张图片。")
            items = [rows[x] for x in dict.fromkeys(ids)]
            inputs = {"items": items}
            pages, maximum = 1, len(items)
        elif mode == "query":
            filters = self.filters(body.get("filters", {}))
            if not any(key in filters for key in ("character", "artist", "pack", "q")):
                raise CacheError("批量缓存请至少填写人物、画师、表情包或关键词之一。")
            pages = self.number(body.get("pages", 1), 1, 5)
            start = self.number(body.get("start_page", 1), 1, 100000)
            maximum = self.number(body.get("maximum", 20), 1, 100)
            inputs = {"filters": filters, "start": start}
        else:
            raise CacheError("缓存任务类型无效。")
        identifier = secrets.token_hex(16)
        job = {
            "id": identifier,
            "owner": owner,
            "request_id": request_id,
            "state": "running",
            "done": 0,
            "success": 0,
            "failed": 0,
            "total": maximum,
            "pages_done": 0,
            "pages": pages,
            "cancel_requested": False,
            "errors": [],
        }
        self.jobs[identifier] = job
        while len(self.jobs) > 20:
            self.jobs.popitem(last=False)
        self.task = asyncio.create_task(self.run_job(job, category, inputs))
        return json_response(self.job_status(job))

    async def run_job(self, job, category, inputs):
        """Run downloads sequentially, preserving partial success and cancellation.

        Args:
            job: Mutable internal progress state.
            category: Existing local usage category.
            inputs: Private selected records or bounded query inputs.
        """
        try:
            size = max(1, min(20, int(self.plugin.config.get("cache_batch_size", 8))))
            for offset in range(job["pages"]):
                if job["cancel_requested"] or job["done"] >= job["total"]:
                    break
                has_next = False
                if "items" in inputs:
                    items = inputs["items"]
                else:
                    result = await self.plugin.request_api(
                        "/emojis",
                        inputs["filters"]
                        | self.plugin.media_params()
                        | {
                            "page": inputs["start"] + offset,
                            "size": min(size, job["total"] - job["done"]),
                        },
                    )
                    items = result.get("items")
                    has_next = bool(result.get("hasNext"))
                    if (
                        not isinstance(items, list)
                        or len(items) > size
                        or not all(isinstance(x, dict) for x in items)
                    ):
                        raise CacheError("小站返回的批量列表无效。")
                for item in items:
                    if job["cancel_requested"] or job["done"] >= job["total"]:
                        break
                    try:
                        await self.plugin.cache_image(item, category, required=True)
                        job["success"] += 1
                    except Exception as exc:
                        job["failed"] += 1
                        if len(job["errors"]) < 10:
                            job["errors"].append(
                                str(exc)
                                if isinstance(exc, CacheError)
                                else "该图片缓存失败，请检查网络与 API 状态。"
                            )
                    job["done"] += 1
                job["pages_done"] += 1
                if not has_next:
                    break
            job["state"] = "cancelled" if job["cancel_requested"] else "completed"
        except asyncio.CancelledError:
            job["state"] = "cancelled"
            raise
        except Exception as exc:
            job["state"] = "failed"
            job["errors"].append(
                str(exc)
                if isinstance(exc, CacheError)
                else "获取后续页面失败；已经缓存成功的图片已保留。"
            )

    async def close(self):
        """Stop outstanding jobs before the plugin closes its HTTP session."""
        if self.task and not self.task.done():
            for job in self.jobs.values():
                job["cancel_requested"] = True
            try:
                await asyncio.wait_for(asyncio.shield(self.task), timeout=3)
            except TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
        self.snapshots.clear()
        self.previews.clear()
