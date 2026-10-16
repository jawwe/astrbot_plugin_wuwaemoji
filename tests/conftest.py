"""Keep AstrBot import-time runtime files inside the ignored test cache."""

import os
from pathlib import Path

os.environ["ASTRBOT_ROOT"] = str(
    Path(__file__).resolve().parents[1] / ".pytest_cache" / "astrbot-runtime"
)
