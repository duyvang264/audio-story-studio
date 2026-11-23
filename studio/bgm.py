"""Procedural dark-ambient background music (self-generated, no licensing issues)."""
from __future__ import annotations

from pathlib import Path

from . import media


def generate_dark_ambient(out: Path, seconds: int = 240, seed: int = 7) -> Path:
    """Low drones (A1/E2/A2) with slow tremolo over filtered brown noise, echo tail, -24 LUFS."""
    out.parent.mkdir(parents=True, exist_ok=True)
    d = str(seconds)
    graph = (
        f"anoisesrc=c=brown:a=0.4:d={d}:r=48000:s={seed},lowpass=f=260,volume=0.6[n];"
        f"sine=f=55:d={d}:r=48000,tremolo=f=0.1:d=0.6,volume=0.5[s1];"
        f"sine=f=82.41:d={d}:r=48000,tremolo=f=0.13:d=0.7,volume=0.3[s2];"
        f"sine=f=110:d={d}:r=48000,tremolo=f=0.17:d=0.8,volume=0.15[s3];"
        "[n][s1][s2][s3]amix=inputs=4:normalize=0,"
        "aecho=0.8:0.6:700|1100:0.35|0.25,lowpass=f=1800,"
        f"afade=t=in:d=4,afade=t=out:st={seconds - 4}:d=4,"
        "loudnorm=I=-24:TP=-3:LRA=7,aformat=channel_layouts=stereo[a]"
    )
    media.ffmpeg(["-filter_complex", graph, "-map", "[a]", "-t", d, "-ar", "48000", "-c:a", "aac", "-b:a", "128k", str(out)],
                 timeout=600)
    return out
