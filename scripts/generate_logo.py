"""Render the project's original geometric speech-and-wave icon."""

import math
from pathlib import Path

from PIL import Image, ImageDraw

scale = 4
image = Image.new("RGB", (256 * scale, 256 * scale), "#101c30")
draw = ImageDraw.Draw(image)
gold = "#f4d48b"
draw.rounded_rectangle(
    (32 * scale, 38 * scale, 224 * scale, 204 * scale),
    radius=46 * scale,
    fill="#213a52",
    outline=gold,
    width=5 * scale,
)
draw.polygon(
    [(64 * scale, 190 * scale), (58 * scale, 226 * scale), (103 * scale, 200 * scale)],
    fill=gold,
)
for x in (99, 157):
    draw.ellipse(((x - 5) * scale, 77 * scale, (x + 5) * scale, 87 * scale), fill=gold)
for offset in (0, 23, 46):
    points = [
        (x * scale, (112 + offset + 7 * math.sin((x - 68) / 120 * math.tau)) * scale)
        for x in range(68, 189)
    ]
    draw.line(points, fill=gold, width=5 * scale, joint="curve")
image.resize((256, 256), Image.Resampling.LANCZOS).save(
    Path(__file__).resolve().parents[1] / "logo.png", optimize=True
)
