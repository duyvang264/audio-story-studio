"""Image providers: Mock (procedural, offline), Codex built-in image_gen (headless), Local HTTP adapter.

None of them call a paid API. CodexImageProvider strips OPENAI_API_KEY from the subprocess
environment so Codex cannot fall back to its paid CLI/API image path.
"""
from __future__ import annotations

import hashlib
import math
import os
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import Settings

OUT_W, OUT_H = 1920, 1080


class ImageProviderError(RuntimeError):
    pass


def normalize_image(src: Path, dest: Path, width: int = OUT_W, height: int = OUT_H) -> tuple[int, int]:
    """Cover-crop to exact 16:9 and save as PNG."""
    with Image.open(src) as im:
        im = im.convert("RGB")
        scale = max(width / im.width, height / im.height)
        im = im.resize((math.ceil(im.width * scale), math.ceil(im.height * scale)), Image.LANCZOS)
        left, top = (im.width - width) // 2, (im.height - height) // 2
        im.crop((left, top, left + width, top + height)).save(dest, "PNG")
    return width, height


class ImageProvider:
    name = "base"
    is_paid = False

    def __init__(self, settings: Settings):
        self.settings = settings

    def generate(self, prompt: str, negative: str, out_path: Path) -> dict:
        raise NotImplementedError


class MockImageProvider(ImageProvider):
    """Deterministic moody placeholder art (gradient sky, layered ridges, fog, embers, vignette)."""
    name = "mock"

    PALETTES = {
        "battle": ((40, 8, 4), (150, 50, 15)), "dread": ((6, 12, 10), (50, 70, 55)),
        "sorrow": ((14, 14, 20), (80, 80, 92)), "hope": ((20, 12, 8), (190, 130, 60)),
        "mysterious": ((6, 10, 24), (60, 80, 120)), "calm": ((16, 14, 24), (110, 90, 100)),
    }

    def generate(self, prompt: str, negative: str, out_path: Path, *, mood: str = "mysterious",
                 label: str = "") -> dict:
        seed = int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16)
        rnd = random.Random(seed)
        top, bottom = self.PALETTES.get(mood, self.PALETTES["mysterious"])
        w, h = OUT_W, OUT_H
        img = Image.new("RGB", (w, h))
        px = ImageDraw.Draw(img)
        for y in range(h):
            t = y / h
            px.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t ** 1.5) for i in range(3)))
        # moon / light source
        glow = Image.new("L", (w, h), 0)
        gx, gy, gr = rnd.randint(300, w - 300), rnd.randint(120, 380), rnd.randint(60, 120)
        ImageDraw.Draw(glow).ellipse((gx - gr, gy - gr, gx + gr, gy + gr), fill=200)
        glow = glow.filter(ImageFilter.GaussianBlur(70))
        img = Image.composite(Image.new("RGB", (w, h), (230, 220, 200)), img, glow.point(lambda v: v * 0.6))
        draw = ImageDraw.Draw(img)
        for layer in range(4):
            base_y = int(h * (0.45 + layer * 0.13))
            shade = tuple(max(0, int(c * (0.55 - layer * 0.12))) for c in bottom)
            pts, x = [(0, h)], 0
            y = base_y
            while x <= w:
                y = min(h, max(int(h * 0.3), y + rnd.randint(-60, 60)))
                pts.append((x, y))
                x += rnd.randint(40, 140)
            pts += [(w, y), (w, h)]
            draw.polygon(pts, fill=shade)
            if layer == 1:  # ruined spires
                for _ in range(rnd.randint(2, 4)):
                    sx = rnd.randint(100, w - 100)
                    sh = rnd.randint(120, 320)
                    draw.polygon([(sx - 18, base_y + 20), (sx, base_y - sh), (sx + 18, base_y + 20)], fill=shade)
        fog = Image.new("L", (w, h), 0)
        fd = ImageDraw.Draw(fog)
        for _ in range(40):
            fx, fy = rnd.randint(-200, w), rnd.randint(int(h * 0.45), h)
            fd.ellipse((fx, fy, fx + rnd.randint(300, 700), fy + rnd.randint(40, 120)), fill=rnd.randint(30, 80))
        fog = fog.filter(ImageFilter.GaussianBlur(40))
        img = Image.composite(Image.new("RGB", (w, h), (170, 170, 180)), img, fog)
        draw = ImageDraw.Draw(img)
        for _ in range(140 if mood == "battle" else 50):
            ex, ey, er = rnd.randint(0, w), rnd.randint(int(h * 0.2), h), rnd.choice([1, 1, 2, 3])
            draw.ellipse((ex - er, ey - er, ex + er, ey + er), fill=(255, rnd.randint(120, 190), 60))
        vignette = Image.new("L", (w, h), 0)
        ImageDraw.Draw(vignette).ellipse((-w * 0.15, -h * 0.25, w * 1.15, h * 1.25), fill=255)
        vignette = vignette.filter(ImageFilter.GaussianBlur(180))
        img = Image.composite(img, Image.new("RGB", (w, h), (0, 0, 0)), vignette)
        if label:
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 28)
            except OSError:
                font = ImageFont.load_default()
            ImageDraw.Draw(img).text((40, h - 60), f"MOCK · {label}", fill=(200, 190, 170), font=font)
        img.save(out_path, "PNG")
        return {"provider": self.name, "seed": seed}


class CodexImageProvider(ImageProvider):
    """Codex CLI built-in image_gen via `codex exec` (ChatGPT subscription quota, not the paid API).

    Verified on this machine (codex-cli 0.169.0): images land in
    ~/.codex/generated_images/<session>/exec-*.png and the agent copies one into the workdir.
    """
    name = "codex"
    UNAVAILABLE = "IMAGEGEN_UNAVAILABLE"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        if not settings.codex_imagegen_verified:
            raise ImageProviderError("Codex ImageGen not verified on this machine (CODEX_IMAGEGEN_VERIFIED=false)")
        if shutil.which("codex") is None:
            raise ImageProviderError("codex CLI not found")

    def generate(self, prompt: str, negative: str, out_path: Path, **_) -> dict:
        instruction = (
            "Use ONLY your built-in image_gen tool to generate exactly one image. Never use the CLI/API "
            "fallback, scripts, network downloads or drawing code. "
            f"Image prompt: {prompt}. Avoid: {negative}. "
            "After generating, copy the generated image file into the current working directory as out.png. "
            f"If the built-in image_gen tool is unavailable, reply exactly {self.UNAVAILABLE} and do nothing else."
        )
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "OPENAI_BASE_URL")}
        gen_root = Path(env.get("CODEX_HOME", Path.home() / ".codex")) / "generated_images"
        started = time.time()
        with tempfile.TemporaryDirectory(prefix="codex-img-") as wd:
            try:
                proc = subprocess.run(
                    ["codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "-C", wd, instruction],
                    capture_output=True, text=True, timeout=self.settings.codex_timeout_sec,
                    env=env, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired as e:
                raise ImageProviderError(f"codex exec timed out after {self.settings.codex_timeout_sec}s") from e
            if self.UNAVAILABLE in proc.stdout[-500:]:
                raise ImageProviderError("Codex reported image_gen unavailable in exec mode")
            candidate = Path(wd) / "out.png"
            if not candidate.exists():
                fresh = sorted((p for p in gen_root.glob("*/*.png") if p.stat().st_mtime >= started - 1),
                               key=lambda p: p.stat().st_mtime)
                if not fresh:
                    raise ImageProviderError(f"codex produced no image (exit {proc.returncode}): {proc.stdout[-800:]}")
                candidate = fresh[-1]
            normalize_image(candidate, out_path)
        return {"provider": self.name, "seconds": round(time.time() - started, 1)}


class LocalImageProvider(ImageProvider):
    """Adapter for a local generator (e.g. a ComfyUI/Draw Things bridge) on localhost only.

    Contract: POST {prompt, negative_prompt, width, height} -> image bytes (PNG/JPEG).
    """
    name = "local"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        url = settings.local_image_endpoint
        if not url:
            raise ImageProviderError("LOCAL_IMAGE_ENDPOINT not configured")
        if urlparse(url).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ImageProviderError("LocalImageProvider only talks to localhost endpoints")

    def generate(self, prompt: str, negative: str, out_path: Path, **_) -> dict:
        r = httpx.post(self.settings.local_image_endpoint, timeout=900,
                       json={"prompt": prompt, "negative_prompt": negative, "width": 1344, "height": 768})
        r.raise_for_status()
        tmp = out_path.with_suffix(".raw")
        tmp.write_bytes(r.content)
        try:
            normalize_image(tmp, out_path)
        finally:
            tmp.unlink(missing_ok=True)
        return {"provider": self.name}


def get_image_provider(settings: Settings) -> ImageProvider:
    providers = {"mock": MockImageProvider, "codex": CodexImageProvider, "local": LocalImageProvider}
    return providers[settings.image_provider](settings)
