"""Build an installable ZIP from an explicit list of public plugin files."""

import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
version = re.search(
    r"^version:\s*(\S+)\s*$", (root / "metadata.yaml").read_text(encoding="utf-8"), re.M
).group(1)
output = (
    root
    / f"astrbot_plugin_wuwaemoji-{version if version.startswith('v') else 'v' + version}.zip"
)
files = (
    "main.py",
    "emoji_cache.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "logo.png",
)
with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
    for name in files:
        archive.write(root / name, name)
with ZipFile(output) as archive:
    assert archive.testzip() is None
    assert set(archive.namelist()) == set(files)
assert output.stat().st_size < 16 * 1024 * 1024, (
    "AstrBot market ZIP must be smaller than 16 MiB"
)
print(f"Built {output.name} ({output.stat().st_size} bytes, {len(files)} public files)")
