"""Burned-in subtitles without libass: cues are drawn with Pillow into transparent PNG strips
and fed to ffmpeg as one concat-demuxer image stream that is overlaid on the final video."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]
BAND_RATIO = 0.22      # strip height relative to frame height
FONT_RATIO = 0.046     # font size relative to frame height
MARGIN_RATIO = 0.06    # bottom margin relative to frame height


@lru_cache
def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise RuntimeError("no font with Vietnamese glyphs found for burned subtitles")


def band_height(height: int) -> int:
    return int(height * BAND_RATIO) // 2 * 2


def draw_cue(text: str, width: int, height: int, *, font_px: int | None = None, band_px: int | None = None,
             margin_px: int | None = None) -> Image.Image:
    """Draw one cue into a transparent strip. Defaults suit 16:9; vertical layouts pass explicit sizes."""
    band = band_px or band_height(height)
    img = Image.new("RGBA", (width, band), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = _font(font_px or int(height * FONT_RATIO))
    stroke = max(2, (font_px or int(height * FONT_RATIO)) // 16)
    box = d.multiline_textbbox((0, 0), text, font=font, align="center", spacing=6, stroke_width=stroke)
    tw, th = box[2] - box[0], box[3] - box[1]
    x = (width - tw) // 2 - box[0]
    y = band - (int(height * MARGIN_RATIO) if margin_px is None else margin_px) - th - box[1]
    d.multiline_text((x + 2, y + 3), text, font=font, fill=(0, 0, 0, 150), align="center", spacing=6)
    d.multiline_text((x, y), text, font=font, fill=(245, 238, 225, 255), align="center", spacing=6,
                     stroke_width=stroke, stroke_fill=(0, 0, 0, 230))
    return img


def build_overlay(cues: list[tuple[float, float, str]], width: int, height: int, total_sec: float,
                  out_dir: Path, *, font_px: int | None = None, band_px: int | None = None,
                  margin_px: int | None = None, y: int | None = None) -> tuple[Path, int]:
    """Write cue PNGs and an ffconcat list covering [0, total_sec]. Returns (list path, overlay y)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    band = band_px or band_height(height)
    style = {"font_px": font_px, "band_px": band, "margin_px": margin_px}
    blank = out_dir / "blank.png"
    Image.new("RGBA", (width, band), (0, 0, 0, 0)).save(blank)
    entries: list[tuple[Path, float]] = []
    t = 0.0
    for i, (start, end, text) in enumerate(cues, start=1):
        start, end = max(start, t), min(end, total_sec)
        if end <= start:
            continue
        if start - t > 1e-3:
            entries.append((blank, start - t))
        png = out_dir / f"cue_{i:05d}.png"
        draw_cue(text, width, height, **style).save(png, optimize=True)
        entries.append((png, end - start))
        t = end
    if total_sec - t > 1e-3:
        entries.append((blank, total_sec - t))
    lines = ["ffconcat version 1.0"]
    for path, dur in entries:
        lines += [f"file '{path.name}'", f"duration {dur:.3f}"]
    lines.append(f"file '{entries[-1][0].name}'")  # concat demuxer needs the last file repeated
    lst = out_dir / "subs.ffconcat"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst, height - band if y is None else y
