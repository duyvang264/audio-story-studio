"""Quality Control: streams, duration, frames, black frames, silence, loudness, scene coverage, subtitles."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import media
from .config import Settings, get_settings
from .visual.scene_planner import validate_coverage
from .audio_engine import production as audio_production
from .audio_engine.gate import AudioQCError, ListeningReviewRequired
from .audio_engine.io import identity

LOUDNESS_RANGE = (-20.0, -12.0)
MAX_SILENCE_SEC = 3.0


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    def t(x):
        h, m, rest = x.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000
    out = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 3:
            a, b = re.split(r"\s+-->\s+", lines[1])
            out.append((t(a), t(b), "\n".join(lines[2:])))
    return out


def check_video(episode_id: int, video: Path, *, expect_height: int, settings: Settings | None = None,
                episode_dir: Path | None = None) -> dict:
    s = settings or get_settings()
    d = episode_dir or s.storage / "episodes" / str(episode_id)
    tl = json.loads((d / "timeline.json").read_text(encoding="utf-8"))
    issues, info = [], {}

    def check(cond: bool, msg: str):
        if not cond:
            issues.append(msg)

    check(video.exists() and video.stat().st_size > 0, "video file missing or empty")
    if issues:
        return _write(d, video, expect_height, issues, info)

    p = media.probe(video)
    streams = {st["codec_type"]: st for st in p["streams"]}
    v, a = streams.get("video"), streams.get("audio")
    check(v is not None and v["codec_name"] == "h264", "missing h264 video stream")
    check(a is not None and a["codec_name"] == "aac", "missing aac audio stream")
    if tl["subtitles"].get("mode", "soft") in ("soft", "both"):
        check("subtitle" in streams, "missing subtitle track")
    if v:
        info["resolution"] = f"{v['width']}x{v['height']}"
        check(v["height"] == expect_height and v["width"] == expect_height * 16 // 9,
              f"resolution {info['resolution']} != expected {expect_height * 16 // 9}x{expect_height}")
    dur = float(p["format"]["duration"])
    info["duration_sec"] = dur
    check(abs(dur - tl["duration_sec"]) <= 0.1, f"duration {dur:.3f}s != narration {tl['duration_sec']}s")
    frames = media.video_frame_count(video)
    info["frames"] = frames
    check(abs(frames - tl["total_frames"]) <= 1, f"frame count {frames} != {tl['total_frames']}")

    black = media.black_segments(video)
    info["black_segments"] = black
    check(not black, f"black frames detected: {black[:3]}")

    _audio_check(video, d, s, info, issues)

    try:
        validate_coverage([{"scene_no": sc["scene_no"], "start": sc["start"], "end": sc["end"]}
                           for sc in tl["scenes"]], tl["duration_sec"], tol=0.05)
    except ValueError as e:
        issues.append(f"scene coverage: {e}")
    for sc in tl["scenes"]:
        check(Path(sc["image"]).exists(), f"scene {sc['scene_no']} image missing")

    cues = parse_srt(Path(tl["subtitles"]["srt"]))
    info["subtitle_cues"] = len(cues)
    check(len(cues) > 0, "no subtitles")
    last_end = 0.0
    for i, (a_, b_, text) in enumerate(cues, 1):
        check(b_ > a_ and a_ >= last_end - 1e-3 and b_ <= tl["duration_sec"] + 0.01 and text.strip(),
              f"subtitle cue {i} invalid ({a_}-{b_})")
        check(all(len(line) <= 48 for line in text.splitlines()), f"subtitle cue {i} line too long")
        last_end = b_
    return _write(d, video, expect_height, issues, info)


def _write(d: Path, video: Path, height: int, issues: list[str], info: dict) -> dict:
    report = {"video": str(video), "passed": not issues, "issues": issues, "info": info}
    if video.exists():
        report['file_identity'] = identity(video)
    (d / f"qc_report_{height}p.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


SHORT_LOUDNESS_TOL = 3.0


def check_short(episode_id: int, settings: Settings | None = None, *, episode_dir: Path | None = None) -> dict:
    """QC for the 9:16 TikTok cut: streams, size, exact length, bounds, black frames, loudness, subtitles."""
    s = settings or get_settings()
    d = episode_dir or s.storage / "episodes" / str(episode_id)
    meta = json.loads((d / "tiktok.json").read_text(encoding="utf-8"))
    video = Path(meta["video"])
    issues, info = [], {}

    def check(cond: bool, msg: str):
        if not cond:
            issues.append(msg)

    check(video.exists() and video.stat().st_size > 0, "video file missing or empty")
    if not issues:
        p = media.probe(video)
        streams = {st["codec_type"]: st for st in p["streams"]}
        v, a = streams.get("video"), streams.get("audio")
        check(v is not None and v["codec_name"] == "h264", "missing h264 video stream")
        check(a is not None and a["codec_name"] == "aac", "missing aac audio stream")
        if v:
            info["resolution"] = f"{v['width']}x{v['height']}"
            check((v["width"], v["height"]) == (meta["width"], meta["height"]),
                  f"resolution {info['resolution']} != {meta['width']}x{meta['height']}")
        dur = float(p["format"]["duration"])
        info["duration_sec"] = dur
        check(abs(dur - meta["duration_sec"]) <= 0.1, f"duration {dur:.3f}s != planned {meta['duration_sec']}s")
        if meta["fits_bounds"]:
            lo, hi = s.tiktok_min_sec, s.tiktok_max_sec + 1.0
            check(lo <= dur <= hi, f"duration {dur:.1f}s outside {lo:.0f}-{s.tiktok_max_sec:.0f}s")
        frames = media.video_frame_count(video)
        info["frames"] = frames
        check(abs(frames - meta["total_frames"]) <= 1, f"frame count {frames} != {meta['total_frames']}")
        black = media.black_segments(video)
        info["black_segments"] = black
        check(not black, f"black frames detected: {black[:3]}")
        _audio_check(video, d, s, info, issues, short=True)
        cues = parse_srt(Path(meta["srt"]))
        info["subtitle_cues"] = len(cues)
        check(len(cues) > 0, "no subtitles")
        last_end = 0.0
        for i, (a_, b_, text) in enumerate(cues, 1):
            check(b_ > a_ and a_ >= last_end - 1e-3 and b_ <= meta["duration_sec"] + 0.01 and text.strip(),
                  f"subtitle cue {i} invalid ({a_}-{b_})")
            last_end = b_
    report = {"video": str(video), "passed": not issues, "issues": issues, "info": info}
    if video.exists():
        report['file_identity'] = identity(video)
    (d / "qc_report_tiktok.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _audio_check(video, directory, settings, info, issues, *, short=False):
    """Analyze actual AAC output; unresolved upstream review remains a publishing block."""
    try:
        report = audio_production.encoded_qc(video, directory, settings, short=short)
        info['integrated_lufs'] = report['metrics'].get('integrated_lufs')
        info['true_peak_dbtp'] = report['metrics'].get('true_peak_dbtp')
        info['audio_qc_status'] = report['status']
        info['long_silences'] = [f for f in report['flags'] if f['defect_type'] in ('unexpected_silence', 'missing_narration')]
        issues.extend('audio: ' + f['defect_type'] for f in report['flags'] if f['severity'] == 'critical')
        from .audio_engine.gate import assert_report
        path = directory / 'audio_engine/qc' / ('encoded_tiktok' if short else 'encoded_final') / 'audio_qc_report.json'
        try:
            assert_report(path, video)
            info['publish_gate_status'] = 'PASS'
        except ListeningReviewRequired:
            info['publish_gate_status'] = 'NEEDS_LISTENING_REVIEW'
        except AudioQCError as exc:
            info['publish_gate_status'] = 'FAIL'
            issues.append(str(exc))
    except AudioQCError as exc:
        issues.append(str(exc))
