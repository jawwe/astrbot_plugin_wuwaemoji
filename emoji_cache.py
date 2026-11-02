"""Persistent categorized media storage, independent of AstrBot and API tickets."""

import asyncio
import hashlib
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

DEFAULT_CATEGORIES = {
    "开心": "表达高兴、庆祝、兴奋或收到好消息。",
    "难过": "表达难过、委屈、失落或需要安慰。",
    "赞同": "表示同意、认可、支持、夸赞或点赞。",
    "疑惑": "表示困惑、不解、惊讶或想进一步确认。",
    "生气": "表达不满、生气或轻微抗议，注意聊天语境。",
    "问候": "用于打招呼、早安、晚安、欢迎或告别。",
    "未分类": "尚未整理用途的表情；只按角色或标题选择，不推断其情绪。",
}


class CacheError(Exception):
    """A cache failure safe to show in chat."""


def category_name(value: str) -> str:
    """Validate a logical category name, never interpreting it as a path.

    Args:
        value: Human-readable category name.

    Returns:
        Trimmed category name.

    Raises:
        CacheError: When the category name is empty or unsuitable.
    """
    value = value.strip()
    if value.startswith("角色："):
        raise CacheError("角色分类由小站标签自动维护，请填写情感或用途分类。")
    if (
        not 1 <= len(value) <= 40
        or re.search(r"[/\\\x00-\x1f]", value)
        or value in {".", "..", "全部"}
    ):
        raise CacheError("分类名称须为 1–40 个字符，不能包含斜杠或控制字符。")
    return value


class EmojiCache:
    """Store each image once and organize it through named category memberships."""

    def __init__(
        self, root: Path, max_items: int = 500, max_bytes: int = 256 * 1024 * 1024
    ):
        self.root = root.resolve()
        self.max_items = max_items
        self.max_bytes = max_bytes
        self.lock = asyncio.Lock()
        self.loaded = False
        self.data = {"version": 1, "categories": dict(DEFAULT_CATEGORIES), "items": {}}

    def _load(self):
        self.root.mkdir(parents=True, exist_ok=True)
        index = self.root / "index.json"
        if index.exists():
            try:
                data = json.loads(index.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or (
                    data.get("version") != 1
                    or not isinstance(data["categories"], dict)
                    or not isinstance(data["items"], dict)
                ):
                    raise ValueError
                if not all(
                    isinstance(name, str) and isinstance(description, str)
                    for name, description in data["categories"].items()
                ):
                    raise ValueError
                for key, item in data["items"].items():
                    if not re.fullmatch(r"[a-f0-9]{24}", key) or not isinstance(
                        item, dict
                    ):
                        raise ValueError
                    self._path(item)
                    if not isinstance(item.get("categories"), list):
                        raise ValueError
                    if (
                        not isinstance(item.get("bytes"), int)
                        or item["bytes"] < 0
                        or not isinstance(item.get("updated"), (int, float))
                    ):
                        raise ValueError
                    for field in ("title", "description", "source_id"):
                        if not isinstance(item.get(field), str):
                            raise ValueError
                    for field in ("characters", "artists"):
                        if not isinstance(item.get(field), list) or not all(
                            isinstance(x, dict)
                            and isinstance(x.get("name"), str)
                            and isinstance(x.get("slug"), str)
                            for x in item[field]
                        ):
                            raise ValueError
                self.data = data
            except (ValueError, KeyError, TypeError):
                raise CacheError(
                    "本地表情索引损坏，请管理员备份并检查缓存目录。"
                ) from None
        self.loaded = True

    def _path(self, item: dict) -> Path:
        path = (self.root / item["file"]).resolve()
        if not path.is_relative_to(self.root / "images"):
            raise CacheError("本地表情路径无效。")
        return path

    def _save(self):
        temporary = self.root / "index.json.tmp"
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(self.root / "index.json")

    async def _run(self, operation, *args):
        async with self.lock:
            try:
                if not self.loaded:
                    await asyncio.to_thread(self._load)
                return await asyncio.to_thread(operation, *args)
            except OSError:
                raise CacheError(
                    "无法读写本地表情缓存，请检查磁盘空间和目录权限。"
                ) from None

    async def set_category(self, name: str, description: str):
        """Create or describe a category.

        Args:
            name: Logical category name.
            description: Usage guidance supplied by an administrator.
        """
        name = category_name(name)
        if not 1 <= len(description.strip()) <= 300:
            raise CacheError("请填写 1–300 字的分类用途描述。")

        def update():
            if (
                name not in self.data["categories"]
                and len(self.data["categories"]) >= 200
            ):
                raise CacheError("分类数量已达 200，请整理已有分类。")
            self.data["categories"][name] = description.strip()
            self._save()

        await self._run(update)

    async def put(self, item: dict, content: bytes, category: str = "") -> dict:
        """Validate, deduplicate and atomically store downloaded media.

        Args:
            item: Source API record. Signed URLs are intentionally discarded.
            content: Bounded image bytes.
            category: Optional existing category chosen by an administrator.

        Returns:
            Stored metadata, with a trusted local path.
        """
        if category:
            category = category_name(category)

        def store():
            if category and category not in self.data["categories"]:
                raise CacheError("分类不存在，请先使用 鸣潮图库 分类 名称 用途描述。")
            try:
                with Image.open(io.BytesIO(content)) as image:
                    suffix = {
                        "PNG": "png",
                        "JPEG": "jpg",
                        "GIF": "gif",
                        "WEBP": "webp",
                    }.get(image.format)
                    if suffix is None or image.width * image.height > 25_000_000:
                        raise ValueError
                    animated = bool(getattr(image, "is_animated", False))
                    image.verify()
            except (ValueError, OSError, Image.DecompressionBombError):
                raise CacheError("下载内容不是受支持的安全图片。") from None
            identifier = hashlib.sha256(content).hexdigest()[:24]
            existing = self.data["items"].get(identifier)
            if len(content) > self.max_bytes:
                raise CacheError("该图片超过缓存容量限制。")
            entries = self.data["items"]
            # Deliberately refuse to evict curated images without an explicit clear.
            total = sum(record["bytes"] for record in entries.values())
            if not existing and (
                len(entries) >= self.max_items or total + len(content) > self.max_bytes
            ):
                raise CacheError("表情缓存已满，请管理员清理分类或提高容量限制。")
            characters = item.get("characters") or (
                [item["character"]] if isinstance(item.get("character"), dict) else []
            )
            artists = item.get("artists") or []
            characters = [
                {
                    "name": str(x.get("name", ""))[:80],
                    "slug": str(x.get("slug", ""))[:80],
                }
                for x in characters
                if isinstance(x, dict)
            ][:100]
            artists = [
                {
                    "name": str(x.get("name", ""))[:80],
                    "slug": str(x.get("slug", ""))[:80],
                }
                for x in artists
                if isinstance(x, dict)
            ][:100]
            if existing:
                characters = list(
                    {
                        (x["name"], x["slug"]): x
                        for x in existing["characters"] + characters
                    }.values()
                )[:100]
                artists = list(
                    {
                        (x["name"], x["slug"]): x for x in existing["artists"] + artists
                    }.values()
                )[:100]
            pack = item.get("pack") if isinstance(item.get("pack"), dict) else {}
            source = urlsplit(str(item.get("sourceUrl", "")))
            source_url = (
                f"https://emoji.wuwa.games{source.path}"
                if source.hostname == "emoji.wuwa.games"
                and source.path.startswith("/archives/")
                else ""
            )
            categories = list(existing["categories"]) if existing else []
            if category and category not in categories:
                categories.append(category)
            if not categories:
                categories.append("未分类")
            if category and category != "未分类":
                categories = [x for x in categories if x != "未分类"]
            for character in characters:
                if character["name"]:
                    name = "角色：" + character["name"][:30]
                    if (
                        name not in self.data["categories"]
                        and len(self.data["categories"]) < 200
                    ):
                        self.data["categories"][name] = (
                            f"按上游标签归属的{character['name']}表情，未判定情绪。"
                        )
                    if name in self.data["categories"] and name not in categories:
                        categories.append(name)
            filename = f"images/{identifier}.{suffix}"
            record = {
                "file": filename,
                "bytes": len(content),
                "animated": animated,
                "categories": categories,
                "source_id": str(
                    item.get("id") or (existing or {}).get("source_id", "")
                )[:150],
                "title": str(pack.get("title") or (existing or {}).get("title", ""))[
                    :300
                ],
                "characters": characters,
                "artists": artists,
                "source_url": source_url or (existing or {}).get("source_url", ""),
                "description": existing["description"] if existing else "",
                "updated": time.time(),
            }
            path = self._path(record)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file():
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_bytes(content)
                temporary.replace(path)
            entries[identifier] = record
            self._save()
            return dict(record, id=identifier, path=str(path))

        return await self._run(store)

    async def search(
        self, query: str = "", category: str = "", character: str = "", limit: int = 6
    ) -> list[dict]:
        """Retrieve valid local candidates using category descriptions and metadata.

        Args:
            query: Space-separated literal keywords, not vector search.
            category: Exact logical category, if supplied.
            character: Character name or slug, if supplied.
            limit: Maximum number of returned candidates.

        Returns:
            Matching records with local paths; no source tickets are stored.
        """

        def select():
            result = []
            terms = query.casefold().split()
            for identifier, record in self.data["items"].items():
                if category and category not in record["categories"]:
                    continue
                if character and not any(
                    character.casefold() in {x["name"].casefold(), x["slug"].casefold()}
                    for x in record["characters"]
                ):
                    continue
                text = " ".join(
                    [
                        record["title"],
                        record["description"],
                        *record["categories"],
                        *(
                            self.data["categories"].get(x, "")
                            for x in record["categories"]
                        ),
                        *(
                            x["name"] + " " + x["slug"]
                            for x in record["characters"] + record["artists"]
                        ),
                    ]
                ).casefold()
                if not all(term in text for term in terms):
                    continue
                path = self._path(record)
                if path.is_file():
                    result.append(dict(record, id=identifier, path=str(path)))
            result.sort(key=lambda x: x["updated"], reverse=True)
            return result[: max(1, min(limit, self.max_items))]

        return await self._run(select)

    async def catalog(self) -> dict:
        """Return category guidance, counts and quota use, without local paths.

        Returns:
            Public category and storage summaries.
        """

        def summary():
            records = [
                x for x in self.data["items"].values() if self._path(x).is_file()
            ]
            return {
                "count": len(records),
                "bytes": sum(x["bytes"] for x in records),
                "max_items": self.max_items,
                "max_bytes": self.max_bytes,
                "categories": [
                    {
                        "name": name,
                        "description": description,
                        "count": sum(name in x["categories"] for x in records),
                        "dimension": "character"
                        if name.startswith("角色：")
                        else "usage",
                        "source": "upstream_article_tag"
                        if name.startswith("角色：")
                        else "local_curation",
                    }
                    for name, description in self.data["categories"].items()
                ],
            }

        return await self._run(summary)

    async def get(self, identifier: str) -> dict | None:
        """Resolve only an opaque cache ID, never a caller-supplied file path.

        Args:
            identifier: ID returned by local search.

        Returns:
            Existing cached record, or None if unavailable.
        """

        def lookup():
            record = self.data["items"].get(identifier)
            if record and self._path(record).is_file():
                return dict(record, id=identifier, path=str(self._path(record)))
            return None

        return await self._run(lookup)

    async def classify(self, identifier: str, category: str, description: str = ""):
        """Assign a cached image to a category and optionally describe its content.

        Args:
            identifier: Existing cache ID.
            category: Existing category name.
            description: Optional administrator-written description.
        """
        category = category_name(category)

        def update():
            if category not in self.data["categories"]:
                raise CacheError("分类不存在。")
            record = self.data["items"].get(identifier)
            if not record:
                raise CacheError("缓存 ID 不存在，请先检索图库。")
            record["categories"] = [x for x in record["categories"] if x != "未分类"]
            if category not in record["categories"]:
                record["categories"].append(category)
            if description:
                record["description"] = description.strip()[:300]
            record["updated"] = time.time()
            self._save()

        await self._run(update)

    async def clear(self, category: str) -> int:
        """Remove membership in one category, deleting only unreferenced images.

        Args:
            category: Explicit category to clear.

        Returns:
            Number of images removed from the category.
        """

        def remove():
            if category != "全部" and category not in self.data["categories"]:
                raise CacheError("分类不存在。")
            count = 0
            for identifier, record in list(self.data["items"].items()):
                if category != "全部" and category not in record["categories"]:
                    continue
                if category == "全部":
                    record["categories"].clear()
                else:
                    record["categories"].remove(category)
                count += 1
                if not record["categories"]:
                    self._path(record).unlink(missing_ok=True)
                    del self.data["items"][identifier]
            self._save()
            return count

        return await self._run(remove)
