"""Check the public plugin's metadata, encoding, assets and ZIP layout."""

import json
import re
from pathlib import Path
from zipfile import ZipFile

import yaml
from packaging.specifiers import SpecifierSet
from PIL import Image

root = Path(__file__).resolve().parents[1]
required = {
    "main.py",
    "emoji_cache.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "logo.png",
}
for name in required:
    assert (root / name).is_file(), f"Missing {name}"
    if name != "logo.png":
        data = (root / name).read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM found: {name}"
        data.decode("utf-8")
        assert b"\r\n" not in data, f"Expected LF line endings: {name}"
        assert not re.search(rb"re_[A-Za-z0-9]{20,}\.[A-Za-z0-9_-]{20,}", data), (
            f"API credential found: {name}"
        )
metadata = yaml.safe_load((root / "metadata.yaml").read_text(encoding="utf-8"))
assert re.fullmatch(r"astrbot_plugin_[a-z0-9_]+", metadata["name"])
assert re.fullmatch(r"v?\d+\.\d+\.\d+", str(metadata["version"]))
for field in ("display_name", "desc", "author", "repo"):
    assert isinstance(metadata[field], str) and metadata[field].strip(), (
        f"Missing metadata field: {field}"
    )
assert metadata["repo"] == "https://github.com/jawwe/astrbot_plugin_wuwaemoji"
assert "/" not in metadata["author"]
SpecifierSet(metadata["astrbot_version"])
schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
assert schema["api_token"]["default"] == ""
with Image.open(root / "logo.png") as image:
    assert image.format == "PNG" and image.size == (256, 256)
version = str(metadata["version"])
archives = list(
    root.glob(
        f"astrbot_plugin_wuwaemoji-{version if version.startswith('v') else 'v' + version}.zip"
    )
)
for archive in archives:
    assert archive.stat().st_size < 16 * 1024 * 1024
    with ZipFile(archive) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == required, "Unexpected files in release ZIP"
        for name in required:
            assert package.read(name) == (root / name).read_bytes(), (
                f"Stale archive member: {name}"
            )
print("Plugin metadata, UTF-8/LF, credentials, 256x256 logo and release layout: OK")
