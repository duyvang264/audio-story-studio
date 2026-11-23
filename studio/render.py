"""FFmpeg Engine: per-scene motion clips (cached) + crossfade assembly with narration, BGM, subtitles."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import media, subtitles
from .config import Settings, get_settings
from .qc import parse_srt as qc_parse_srt

RENDERER_VERSION = "r1"
ZOOM = 0.12
PAN_ZOOM = 1.15


def motion_exprs(motion: str, n: int) -> tuple[str, str, str]:
    p = f"(on/{max(n - 1, 1)})"
    center_x, center_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "slow_zoom_in":
        return f"1+{ZOOM}*{p}", center_x, center_y
    if motion == "slow_zoom_out":
        return f"{1 + ZOOM}-{ZOOM}*{p}", center_x, center_y
    if motion == "pan_left_to_right":
        return f"{PAN_ZOOM}", f"(iw-iw/zoom)*{p}", center_y
    if motion == "pan_right_to_left":
        return f"{PAN_ZOOM}", f"(iw-iw/zoom)*(1-{p})", center_y
    raise ValueError(f"unknown motion {motion!r}")


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def render_clip(image: Path, motion: str, frames: int, width: int, height: int, fps: int, crf: int,
                cache_dir: Path) -> tuple[Path, bool]:
    """Render one scene clip. Returns (path, reused_from_cache)."""
    key = hashlib.sha256(json.dumps([RENDERER_VERSION, file_sha(image), motion, frames, width, height, fps, crf])
                         .encode()).hexdigest()[:20]
    out = cache_dir / f"{key}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out, True
    cache_dir.mkdir(parents=True, exist_ok=True)
    z, x, y = motion_exprs(motion, frames)
    w2, h2 = width * 2, height * 2  # oversample so sub-pixel motion stays smooth
    vf = (f"scale={w2}:{h2}:force_original_aspect_ratio=increase,crop={w2}:{h2},"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps},"
          f"setsar=1,format=yuv420p")
    tmp = out.with_suffix(".part.mp4")
    media.ffmpeg(["-i", str(image), "-vf", vf, "-frames:v", str(frames), "-r", str(fps),
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-tune", "stillimage",
                  "-g", str(fps * 4), "-an", str(tmp)], timeout=3600)
    tmp.replace(out)
    return out, False


def xfade_graph(frames: list[int], tf: int, fps: int, out_label: str) -> list[str]:
    """Chain inputs 0..n-1 with crossfades. Clip k (except the last) carries tf extra frames,
    so the output length is exactly sum(frames)."""
    n = len(frames)
    if n == 1 or tf == 0:
        return ["".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0{out_label}"]
    graph, prev, offset_frames = [], "[0:v]", 0
    for k in range(1, n):
        offset_frames += frames[k - 1]
        label = out_label if k == n - 1 else f"[v{k}]"
        graph.append(f"{prev}[{k}:v]xfade=transition=fade:duration={tf / fps:.3f}:offset={offset_frames / fps:.3f}"
                     f"{label}")
        prev = label
    return graph


def render_episode(episode_id: int, *, height: int, out_path: Path, crf: int,
                   settings: Settings | None = None, episode_dir: Path | None = None) -> dict:
    s = settings or get_settings()
    d = episode_dir or s.storage / "episodes" / str(episode_id)
    tl = json.loads((d / "timeline.json").read_text(encoding="utf-8"))
    from .audio_engine.production import audio_fields, prepare_episode
    try:
        fields = audio_fields(d)
    except Exception as exc:
        from .audio_engine.gate import AudioQCError
        if not isinstance(exc, AudioQCError):
            raise
        prepare_episode(episode_id, s, d)
        fields = audio_fields(d)
    tl['audio'].update(fields)
    width = height * 16 // 9
    fps = tl["fps"]
    trans = float(tl["transition"]["duration_sec"])
    tf = round(trans * fps)
    scenes = tl["scenes"]
    started = time.time()

    clips, reused = [], 0
    for i, sc in enumerate(scenes):
        extra = tf if i < len(scenes) - 1 else 0
        clip, hit = render_clip(Path(sc["image"]), sc["motion"], sc["frames"] + extra, width, height, fps, crf,
                                d / "clips" / f"{height}p")
        clips.append(clip)
        reused += hit

    total_sec = tl["total_frames"] / fps
    args: list[str] = []
    for c in clips:
        args += ["-i", str(c)]
    narration_idx = len(clips)
    args += ["-i", tl["audio"]["master"]]
    bgm_idx = None
    if tl["audio"].get("bgm") and not tl['audio'].get('master'):
        bgm_idx = narration_idx + 1
        args += ["-stream_loop", "-1", "-i", tl["audio"]["bgm"]]
    srt = tl["subtitles"]["srt"]
    mode = tl["subtitles"].get("mode", "soft")
    next_idx = narration_idx + 1 + (1 if bgm_idx is not None else 0)
    sub_idx = burn_idx = None
    if mode in ("soft", "both"):
        sub_idx = next_idx
        args += ["-i", srt]
        next_idx += 1
    if mode in ("burn", "both"):
        burn_idx = next_idx
        cues = qc_parse_srt(Path(srt))
        lst, burn_y = subtitles.build_overlay(cues, width, height, total_sec, d / "subs" / f"{height}p")
        args += ["-f", "concat", "-safe", "0", "-i", str(lst)]

    base = "[vbase]" if burn_idx is not None else "[vout]"
    graph = xfade_graph([sc["frames"] for sc in scenes], tf, fps, base)
    if burn_idx is not None:
        graph.append(f"[{burn_idx}:v]format=rgba[subs]")
        graph.append(f"[vbase][subs]overlay=0:{burn_y}:eof_action=pass:format=auto[vout]")
    if bgm_idx is not None:
        vol = tl["audio"].get("bgm_volume", 0.12)
        graph.append(f"[{bgm_idx}:a]volume={vol},afade=t=in:d=2,afade=t=out:st={max(total_sec - 3, 0):.3f}:d=3[bgm]")
        graph.append(f"[{narration_idx}:a][bgm]amix=inputs=2:duration=first:normalize=0[aout]")
        amap = "[aout]"
    else:
        amap = f"{narration_idx}:a"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".part.mp4")
    args += ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", amap]
    if sub_idx is not None:
        args += ["-map", f"{sub_idx}:s", "-c:s", "mov_text", "-metadata:s:s:0", "language=vie"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(fps),
             "-c:a", "aac", "-b:a", s.audio.aac_bitrate, "-ar", str(s.audio.sample_rate),
             "-t", f"{total_sec:.3f}", "-movflags", "+faststart", str(tmp)]
    media.ffmpeg(args, timeout=4 * 3600)
    tmp.replace(out_path)
    return {"path": str(out_path), "width": width, "height": height, "clips": len(clips),
            "clips_reused": reused, "seconds": round(time.time() - started, 1)}
