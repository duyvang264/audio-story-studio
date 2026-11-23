"""TikTok / Shorts: pick the climax window of an episode (whole paragraphs, 60-120 s) and render it 9:16
with the same scene images, a title header and large burned-in subtitles."""
from __future__ import annotations

import json
import time
from pathlib import Path

from PIL import Image, ImageDraw

from . import media, subtitles
from .config import Settings, get_settings
from .db import connect
from .render import render_clip, xfade_graph
from .timeline import build_cues, write_srt

LINE_CHARS = 22          # vertical frame: short subtitle lines
MIN_SEGMENT_SEC = 1.5    # scene slices shorter than this are merged into a neighbour
LEAD_SEC, TAIL_SEC = 0.4, 0.6


def _paragraph_flags(chunks: list[dict]) -> tuple[list[bool], list[bool]]:
    key = [(c["section_no"], c["paragraph_no"]) for c in chunks]
    starts = [i == 0 or key[i] != key[i - 1] for i in range(len(chunks))]
    ends = [i == len(chunks) - 1 or starts[i + 1] for i in range(len(chunks))]
    return starts, ends


def select_window(chunks: list[dict], scenes: list[dict], *, min_sec: float, max_sec: float,
                  target_sec: float, total_sec: float) -> dict:
    """Choose the most intense run of whole paragraphs lasting min..max seconds (closest to target).
    Falls back to sentence boundaries, then to the opening of the episode if nothing fits."""
    if not chunks:
        raise ValueError("no narration chunks")
    peak = max((float(sc["intensity"]) for sc in scenes), default=0) or 1.0

    def intensity(a: float, b: float) -> float:
        acc = sum(max(0.0, min(b, sc["end"]) - max(a, sc["start"])) * float(sc["intensity"]) for sc in scenes)
        return acc / max(b - a, 1e-6) / peak

    starts, ends = _paragraph_flags(chunks)
    best = None
    for strict in (True, False):
        for i in range(len(chunks)):
            if strict and not starts[i]:
                continue
            for j in range(i, len(chunks)):
                dur = chunks[j]["end"] - chunks[i]["start"]
                if dur > max_sec:
                    break
                if dur < min_sec or (strict and not ends[j]):
                    continue
                score = intensity(chunks[i]["start"], chunks[j]["end"]) - 0.5 * abs(dur - target_sec) / target_sec
                if best is None or score > best[0]:
                    best = (score, i, j)
        if best:
            break
    if best is None:  # narration shorter than min_sec (or one huge sentence): take the opening up to max_sec
        i, j = 0, 0
        while j + 1 < len(chunks) and chunks[j + 1]["end"] - chunks[0]["start"] <= max_sec:
            j += 1
        best = (None, i, j)
    _, i, j = best
    a = max(0.0, chunks[i]["start"] - LEAD_SEC)
    b = min(total_sec, chunks[j]["end"] + TAIL_SEC)
    return {"start": round(a, 3), "end": round(b, 3), "first_chunk": chunks[i]["chunk_no"],
            "last_chunk": chunks[j]["chunk_no"], "chunks": chunks[i:j + 1],
            "fits_bounds": min_sec <= b - a <= max_sec + LEAD_SEC + TAIL_SEC}


def slice_scenes(scenes: list[dict], a: float, b: float, fps: int) -> list[dict]:
    """Scene slices inside [a, b] with frame counts that sum to round((b-a)*fps)."""
    segs = [{**sc, "start": max(sc["start"], a), "end": min(sc["end"], b)}
            for sc in scenes if min(sc["end"], b) - max(sc["start"], a) > 0]
    merged: list[dict] = []
    for sg in segs:
        if merged and sg["end"] - sg["start"] < MIN_SEGMENT_SEC:
            merged[-1]["end"] = sg["end"]
        elif merged and merged[-1]["end"] - merged[-1]["start"] < MIN_SEGMENT_SEC:
            merged[-1] = {**sg, "start": merged[-1]["start"]}
        else:
            merged.append(sg)
    for sg in merged:
        sg["frames"] = round((sg["end"] - a) * fps) - round((sg["start"] - a) * fps)
    return [sg for sg in merged if sg["frames"] > 0]


def draw_header(title: str, subtitle: str, width: int, height: int) -> Image.Image:
    """Top gradient band with the story title, shown for the whole short."""
    band = int(height * 0.17) // 2 * 2
    img = Image.new("RGBA", (width, band), (0, 0, 0, 0))
    grad = Image.new("L", (1, band))
    grad.putdata([int(190 * (1 - y / band) ** 1.4) for y in range(band)])
    img.paste(Image.new("RGBA", (width, band), (0, 0, 0, 255)), (0, 0), grad.resize((width, band)))
    d = ImageDraw.Draw(img)
    for text, size, fill, cy in ((title, int(width * 0.068), (236, 196, 120, 255), 0.40),
                                 (subtitle, int(width * 0.040), (230, 225, 215, 255), 0.72)):
        if not text:
            continue
        font = subtitles._font(size)
        while d.textlength(text, font=font) > width * 0.9 and size > 12:
            size -= 2
            font = subtitles._font(size)
        box = d.textbbox((0, 0), text, font=font, stroke_width=max(2, size // 16))
        x = (width - (box[2] - box[0])) // 2 - box[0]
        y = int(band * cy) - (box[3] - box[1]) // 2 - box[1]
        d.text((x, y), text, font=font, fill=fill, stroke_width=max(2, size // 16), stroke_fill=(0, 0, 0, 220))
    return img


def short_paths(episode_id: int, s: Settings) -> tuple[Path, Path]:
    width = s.tiktok_height * 9 // 16 // 2 * 2
    out = s.storage / "output" / str(episode_id) / f"tiktok_{width}x{s.tiktok_height}.mp4"
    return out, s.storage / "episodes" / str(episode_id) / "tiktok.json"


def render_short(episode_id: int, settings: Settings | None = None, *, episode_dir: Path | None = None,
                 out_path: Path | None = None) -> dict:
    s = settings or get_settings()
    d = episode_dir or s.storage / "episodes" / str(episode_id)
    tl = json.loads((d / "timeline.json").read_text(encoding="utf-8"))
    tr = json.loads((d / "transcript_timeline.json").read_text(encoding="utf-8"))
    script = json.loads((d / "episode_script.json").read_text(encoding="utf-8"))
    if episode_dir is not None:
        rows = json.loads((d / 'scene_plan.json').read_text())['scenes']
    else:
        with connect() as c:
            rows = c.execute("SELECT scene_no, intensity FROM scenes WHERE episode_id=%s", (episode_id,)).fetchall()
    intensity = {r["scene_no"]: float(r["intensity"]) for r in rows}
    scenes = [{**sc, "intensity": intensity.get(sc["scene_no"], 0.0)} for sc in tl["scenes"]]
    started = time.time()

    win = select_window(tr["chunks"], scenes, min_sec=s.tiktok_min_sec, max_sec=s.tiktok_max_sec,
                        target_sec=s.tiktok_target_sec, total_sec=tl["duration_sec"])
    a, fps = win["start"], tl["fps"]
    total_frames = round((win["end"] - a) * fps)
    dur = total_frames / fps
    from .audio_engine.production import audio_fields
    from .audio_engine.mastering import master
    from .audio_engine.io import identity, stable_hash
    tl['audio'].update(audio_fields(d))
    cut_key = stable_hash([identity(tl['audio']['master']), a, dur])[:20]
    cut = d / 'audio_engine' / 'shorts' / f'{cut_key}.wav'
    cut.parent.mkdir(parents=True, exist_ok=True)
    if not cut.exists():
        media.ffmpeg(['-ss', str(a), '-i', tl['audio']['master'], '-t', str(dur), '-c:a', 'pcm_f32le', str(cut)])
    short_master = master(cut, d / 'audio_engine' / 'short_masters',
                          s.audio.model_copy(update={'target_lufs': s.tiktok_lufs}))
    segs = slice_scenes(scenes, a, win["end"], fps)
    height = s.tiktok_height
    width = height * 9 // 16 // 2 * 2
    tf = min(round(float(tl["transition"]["duration_sec"]) * fps),
             min((sg["frames"] for sg in segs), default=0) // 2) if len(segs) > 1 else 0

    clips = []
    for k, sg in enumerate(segs):
        clip, _ = render_clip(Path(sg["image"]), sg["motion"], sg["frames"] + (tf if k < len(segs) - 1 else 0),
                              width, height, fps, 20, d / "clips" / "tiktok")
        clips.append(clip)

    shifted = [{**ch, "start": max(ch["start"] - a, 0.0), "end": min(ch["end"] - a, dur)} for ch in win["chunks"]]
    cues = [cu for cu in build_cues(shifted, LINE_CHARS) if cu["end"] > cu["start"]]
    srt = d / "tiktok.srt"
    write_srt(cues, srt)
    sub_dir = d / "subs" / "tiktok"
    font_px = int(width * 0.064)
    lst, sub_y = subtitles.build_overlay([(cu["start"], cu["end"], cu["text"]) for cu in cues], width, height, dur,
                                         sub_dir, font_px=font_px, band_px=int(font_px * 3.6) // 2 * 2,
                                         margin_px=int(font_px * 0.4), y=int(height * 0.62))
    header = sub_dir / "header.png"
    story_title = script.get("story_title") or script["title"]
    tagline = script["title"].replace(story_title, "").strip(" —-") or "Truyện audio · Dark Fantasy"
    draw_header(story_title, tagline, width, height).save(header)

    args: list[str] = []
    for cl in clips:
        args += ["-i", str(cl)]
    n = len(clips)
    nar, hdr, sub = n, n + 1, n + 2
    args += ["-i", short_master['output']['path'],
             "-loop", "1", "-t", f"{dur:.3f}", "-i", str(header),
             "-f", "concat", "-safe", "0", "-i", str(lst)]
    graph = xfade_graph([sg["frames"] for sg in segs], tf, fps, "[vbase]")
    graph += [f"[{hdr}:v]format=rgba[hdr]", "[vbase][hdr]overlay=0:0:format=auto[vh]",
              f"[{sub}:v]format=rgba[subs]", f"[vh][subs]overlay=0:{sub_y}:eof_action=pass:format=auto[vout]",
              f"[{nar}:a]anull[nar]"]
    voice = "[nar]"
    if tl["audio"].get("bgm") and not tl['audio'].get('master'):
        args += ["-stream_loop", "-1", "-i", tl["audio"]["bgm"]]
        vol = tl["audio"].get("bgm_volume", 0.12)
        graph += [f"[{sub + 1}:a]volume={vol},afade=t=in:d=1,afade=t=out:st={max(dur - 1.5, 0):.3f}:d=1.5[bgm]",
                  "[nar][bgm]amix=inputs=2:duration=first:normalize=0[mix]"]
        voice = "[mix]"
    graph.append(f"{voice}anull[aout]")

    out, meta_path = short_paths(episode_id, s)
    if out_path is not None:
        out = out_path
    if episode_dir is not None:
        meta_path = d / 'tiktok.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    args += ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(fps),
             "-c:a", "aac", "-b:a", s.audio.aac_bitrate, "-ar", str(s.audio.sample_rate),
             "-t", f"{dur:.3f}", "-movflags", "+faststart", str(tmp)]
    media.ffmpeg(args, timeout=3600)
    tmp.replace(out)
    meta = {"episode_id": episode_id, "video": str(out), "width": width, "height": height, "fps": fps,
            "source_start": a, "source_end": round(a + dur, 3), "duration_sec": round(dur, 3),
            "total_frames": total_frames, "first_chunk": win["first_chunk"], "last_chunk": win["last_chunk"],
            "fits_bounds": win["fits_bounds"], "scenes": [sg["scene_no"] for sg in segs],
            "srt": str(srt), "cues": len(cues), "target_lufs": s.tiktok_lufs,
            "short_master_report": short_master['qc_report']}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(out), "duration_sec": meta["duration_sec"], "window": [a, meta["source_end"]],
            "scenes": meta["scenes"], "seconds": round(time.time() - started, 1)}
