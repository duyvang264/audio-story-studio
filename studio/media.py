"""Thin, safe wrappers around ffmpeg/ffprobe (argument arrays only, never shell=True)."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


class MediaError(RuntimeError):
    pass


def run(args: list[str], *, timeout: float = 3600) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise MediaError(f"{args[0]} failed ({proc.returncode}): {proc.stderr[-2000:]}")
    return proc


def ffmpeg(args: list[str], *, timeout: float = 3600) -> subprocess.CompletedProcess:
    bounded = []
    for arg in args:
        if arg == '-i':
            bounded.extend(['-threads', '2'])
        bounded.append(arg)
    return run([FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                '-filter_complex_threads', '2', '-filter_threads', '2', *bounded], timeout=timeout)


def probe(path: Path | str) -> dict:
    proc = run([FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=120)
    return json.loads(proc.stdout)


def duration(path: Path | str) -> float:
    return float(probe(path)["format"]["duration"])


def video_frame_count(path: Path | str) -> int:
    proc = run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
                "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)], timeout=600)
    return int(proc.stdout.strip().split(",")[0])


def analyze(path: Path | str, filters: str, *, audio: bool, timeout: float = 3600) -> str:
    """Run an analysis filter and return ffmpeg's stderr (info level)."""
    flag = "-af" if audio else "-vf"
    proc = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(path), flag, filters,
                           "-f", "null", "-"], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise MediaError(proc.stderr[-2000:])
    return proc.stderr


def silences(path: Path | str, *, noise_db: float = -45, min_sec: float = 2.5) -> list[tuple[float, float]]:
    err = analyze(path, f"silencedetect=noise={noise_db}dB:d={min_sec}", audio=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(starts, ends))


def black_segments(path: Path | str, *, min_sec: float = 0.3) -> list[tuple[float, float]]:
    err = analyze(path, f"blackdetect=d={min_sec}:pix_th=0.05:pic_th=0.98", audio=False)
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]


def integrated_loudness(path: Path | str) -> float | None:
    err = analyze(path, "ebur128=framelog=quiet", audio=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    return float(m[-1]) if m else None
