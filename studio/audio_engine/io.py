"""Argument-array FFmpeg I/O, immutable identities and atomic JSON artifacts."""
from __future__ import annotations
import hashlib
import json
import subprocess
from pathlib import Path
import numpy as np
from .. import media


def digest(path: Path | str) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def identity(path: Path | str) -> dict:
    p = Path(path).resolve()
    return {"path": str(p), "sha256": digest(p), "size_bytes": p.stat().st_size}


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def decode(path: Path | str, rate: int | None = None, channels: int | None = None) -> tuple[np.ndarray, int]:
    p = media.probe(path)
    streams = [s for s in p["streams"] if s["codec_type"] == "audio"]
    if not streams:
        raise media.MediaError("file has no audio stream")
    rate = rate or int(streams[0]["sample_rate"])
    channels = channels or int(streams[0]["channels"])
    if channels not in (1, 2):
        raise media.MediaError("audio analysis supports mono/stereo; downmix explicitly before analysis")
    args = [media.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
            "-map", "0:a:0", "-vn", "-ar", str(rate), "-ac", str(channels), "-f", "f32le", "pipe:1"]
    r = subprocess.run(args, capture_output=True, timeout=3600)
    if r.returncode:
        raise media.MediaError("audio decode failed: " + r.stderr.decode(errors="replace")[-1000:])
    a = np.frombuffer(r.stdout, dtype="<f4").reshape(-1, channels).copy()
    if not len(a) or not np.isfinite(a).all():
        raise media.MediaError("empty or non-finite decoded audio")
    return a, rate


def write(path: Path, samples: np.ndarray, rate: int, codec: str = "pcm_s24le") -> Path:
    a = np.asarray(samples, dtype="<f4")
    if a.ndim == 1:
        a = a[:, None]
    if not np.isfinite(a).all():
        raise ValueError("refusing non-finite audio")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".part" + path.suffix)
    args = [media.FFMPEG, "-hide_banner", "-nostdin", "-y", "-v", "error", "-f", "f32le",
            "-ar", str(rate), "-ac", str(a.shape[1]), "-i", "pipe:0", "-c:a", codec, str(tmp)]
    r = subprocess.run(args, input=a.tobytes(), capture_output=True, timeout=3600)
    if r.returncode:
        raise media.MediaError("audio encode failed: " + r.stderr.decode(errors="replace")[-1000:])
    tmp.replace(path)
    return path


def db(value: float) -> float:
    return float(20 * np.log10(max(float(value), 1e-12)))
