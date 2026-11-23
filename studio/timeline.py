"""Timeline Composer: frame-exact scene timing, subtitles, audio and effects in one timeline.json."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from .config import Settings, get_settings
from .db import connect

SUB_LINE_CHARS = 42
SUB_MAX_LINES = 2


def split_cue_text(text: str, line_chars: int = SUB_LINE_CHARS) -> list[str]:
    """Split a sentence into subtitle cues of at most 2 lines x line_chars."""
    lines = textwrap.wrap(text, line_chars, break_long_words=False) or [text]
    return ["\n".join(lines[i:i + SUB_MAX_LINES]) for i in range(0, len(lines), SUB_MAX_LINES)]


def build_cues(chunks: list[dict], line_chars: int = SUB_LINE_CHARS) -> list[dict]:
    cues = []
    for ch in chunks:
        parts = split_cue_text(ch["text"], line_chars)
        total_chars = sum(len(p) for p in parts) or 1
        t = ch["start"]
        span = ch["end"] - ch["start"]
        for p in parts:
            dur = span * len(p) / total_chars
            cues.append({"start": round(t, 3), "end": round(t + dur, 3), "text": p})
            t += dur
    return cues


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[dict], path: Path) -> None:
    lines = []
    for i, c in enumerate(cues, start=1):
        lines += [str(i), f"{srt_time(c['start'])} --> {srt_time(c['end'])}", c["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def scene_frames(scenes: list[dict], fps: int) -> list[int]:
    """Frame counts derived from rounded boundaries so their sum equals round(total*fps) exactly."""
    return [round(float(s["end"]) * fps) - round(float(s["start"]) * fps) for s in scenes]


def compose(episode_id: int, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    d = s.storage / "episodes" / str(episode_id)
    transcript = json.loads((d / "transcript_timeline.json").read_text(encoding="utf-8"))
    with connect() as c:
        rows = c.execute("""SELECT s.*, a.path AS asset_path FROM scenes s LEFT JOIN visual_assets a ON a.id=s.asset_id
                            WHERE s.episode_id=%s ORDER BY s.scene_no""", (episode_id,)).fetchall()
    if not rows:
        raise RuntimeError("no scenes planned")
    missing = [r["scene_no"] for r in rows if not r["asset_path"] or not Path(r["asset_path"]).exists()]
    if missing:
        raise RuntimeError(f"scenes without an image file: {missing}")
    frames = scene_frames([{"start": r["start_sec"], "end": r["end_sec"]} for r in rows], s.fps)
    total_frames = round(float(transcript["duration_sec"]) * s.fps)
    if sum(frames) != total_frames:
        raise RuntimeError(f"scene frames {sum(frames)} != narration frames {total_frames}")
    cues = build_cues(transcript["chunks"])
    srt = d / "subtitles.srt"
    write_srt(cues, srt)
    bgm_file = s.project_path(s.bgm_path)
    bgm = str(bgm_file) if bgm_file and bgm_file.exists() else None
    timeline = {
        "episode_id": episode_id,
        "fps": s.fps,
        "duration_sec": round(total_frames / s.fps, 3),
        "total_frames": total_frames,
        "transition": {"type": "fade", "duration_sec": s.transition_sec if len(rows) > 1 else 0},
        "audio": {"narration": transcript["audio_path"], "bgm": bgm, "bgm_volume": s.bgm_volume},
        "subtitles": {"srt": str(srt), "cues": len(cues), "mode": s.subtitle_mode},
        "scenes": [{"scene_no": r["scene_no"], "start": float(r["start_sec"]), "end": float(r["end_sec"]),
                    "frames": f, "image": r["asset_path"], "motion": r["motion"], "version": r["version"]}
                   for r, f in zip(rows, frames)],
    }
    from .audio_engine.production import audio_fields
    timeline['audio'].update(audio_fields(d))
    (d / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"scenes": len(rows), "duration_sec": timeline["duration_sec"], "cues": len(cues)}
