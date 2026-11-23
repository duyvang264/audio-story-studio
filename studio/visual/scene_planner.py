"""Scene planning: content-aware scene boundaries on real audio timestamps.

Boundaries are chosen among paragraph ends (falling back to sentence ends) by dynamic
programming that keeps every scene within [min, max] seconds, close to the target, and
rewards boundaries where the story changes (section break, location change, cast change).
Scenes are contiguous: scene[i].end == scene[i+1].start, first starts at 0, last ends at
the narration duration — no gaps, no overlaps.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..story import find_mentions

MOOD_LEXICON = {
    "battle": ["chém", "nhát kiếm", "rút kiếm", "giao chiến", "máu", "lao tới", "gầm", "đâm", "vung",
               "bão tro", "bốc cháy", "thiêu rụi", "tấn công"],
    "dread": ["sợ", "kinh hoàng", "rùng mình", "bóng tối", "quỷ", "xác", "lạnh toát", "thét", "run rẩy", "ma", "chết chóc"],
    "sorrow": ["khóc", "nước mắt", "mất", "tang", "tiếc", "đau", "cô độc", "từ biệt", "nấm mồ"],
    "hope": ["ánh sáng", "hy vọng", "bình minh", "ấm áp", "mỉm cười", "sống sót", "thắp"],
    "mysterious": ["sương", "bí ẩn", "thì thầm", "cổ xưa", "phong ấn", "ký tự", "kỳ lạ", "chuông"],
    "calm": ["yên", "tĩnh lặng", "gió nhẹ", "nghỉ", "lặng lẽ"],
}
INTENSITY_WEIGHT = {"battle": 1.0, "dread": 0.8, "sorrow": 0.4, "hope": 0.3, "mysterious": 0.2, "calm": 0.0}
MOTIONS = ["slow_zoom_in", "slow_zoom_out", "pan_left_to_right", "pan_right_to_left"]
MOOD_MOTION = {"battle": "slow_zoom_in", "dread": "slow_zoom_in", "sorrow": "slow_zoom_out",
               "hope": "slow_zoom_out", "mysterious": "pan_left_to_right", "calm": "pan_right_to_left"}
DURATION_SCALE = 25.0


@dataclass
class SceneRules:
    target: float = 120.0
    min: float = 45.0
    max: float = 150.0


def mood_scores(text: str) -> dict[str, int]:
    low = text.lower()
    return {m: sum(len(re.findall(rf"(?<!\w){re.escape(w)}(?!\w)", low)) for w in words)
            for m, words in MOOD_LEXICON.items()}


def _lexicon(script: dict) -> dict:
    def entries(items):
        return [{"id": it["id"], "names": [n.lower() for n in [it["name"], *it.get("aliases", [])] if n]}
                for it in items or []]
    return {"characters": entries(script.get("characters")), "locations": entries(script.get("locations"))}


def annotate_chunks(script: dict, timeline: dict) -> list[dict]:
    """Attach characters, carried-forward location and moods to every timed chunk."""
    lex = _lexicon(script)
    current_loc = None
    out = []
    for ch in timeline["chunks"]:
        locs = find_mentions(ch["text"], lex["locations"])
        if locs:
            current_loc = max(locs, key=locs.get)
        out.append({**ch, "characters": find_mentions(ch["text"], lex["characters"]),
                    "location": current_loc, "moods": mood_scores(ch["text"])})
    return out


def _segment_cost(d: float, r: SceneRules) -> float:
    if d < r.min - 1e-6 or d > r.max + 1e-6:
        return math.inf
    return ((d - r.target) / DURATION_SCALE) ** 2


def choose_boundaries(total: float, candidates: list[tuple[float, float]], r: SceneRules) -> list[float] | None:
    """DP over candidate (time, bonus) points. Returns interior boundary times, or None if infeasible."""
    pts = [(0.0, 0.0)] + sorted(c for c in candidates if 0 < c[0] < total) + [(total, 0.0)]
    n = len(pts)
    best = [math.inf] * n
    prev = [-1] * n
    best[0] = 0.0
    for j in range(1, n):
        for i in range(j - 1, -1, -1):
            d = pts[j][0] - pts[i][0]
            if d > r.max + 1e-6:
                break
            if best[i] == math.inf:
                continue
            cost = best[i] + _segment_cost(d, r) - (pts[j][1] if j < n - 1 else 0)
            if cost < best[j]:
                best[j], prev[j] = cost, i
    if best[-1] == math.inf:
        return None
    out, j = [], prev[n - 1]
    while j > 0:
        out.append(pts[j][0])
        j = prev[j]
    return sorted(out)


def plan_scenes(script: dict, timeline: dict, rules: SceneRules) -> list[dict]:
    total = float(timeline["duration_sec"])
    chunks = annotate_chunks(script, timeline)

    def boundary_time(ch):  # middle of the pause after a chunk
        return round(ch["end"] + ch["pause_after"] / 2, 3)

    para_candidates, sent_candidates = [], []
    for a, b in zip(chunks, chunks[1:]):
        t = boundary_time(a)
        if a["paragraph_no"] == b["paragraph_no"]:
            sent_candidates.append((t, 0.0))
            continue
        bonus = 0.5
        if a["section_no"] != b["section_no"]:
            bonus += 3.0
        if a["location"] != b["location"]:
            bonus += 2.0
        if set(a["characters"]) and set(b["characters"]) and set(a["characters"]) != set(b["characters"]):
            bonus += 1.0
        para_candidates.append((t, bonus))

    if total <= rules.max:
        boundaries: list[float] | None = []  # one scene is valid and preferred for short narrations
    else:
        boundaries = choose_boundaries(total, para_candidates, rules)
        if boundaries is None:
            boundaries = choose_boundaries(total, para_candidates + sent_candidates, rules)
        if boundaries is None:
            relaxed = SceneRules(rules.target, 0.0, rules.max)
            boundaries = choose_boundaries(total, para_candidates + sent_candidates, relaxed)
        if boundaries is None:
            raise ValueError("cannot split narration into scenes within max duration (a sentence is too long?)")

    edges = [0.0, *boundaries, total]
    scenes = []
    prev_motion = None
    for idx, (start, end) in enumerate(zip(edges, edges[1:]), start=1):
        inside = [c for c in chunks if start <= (c["start"] + c["end"]) / 2 < end]
        chars: dict[str, int] = {}
        loc_time: dict[str, float] = {}
        moods: dict[str, int] = {m: 0 for m in MOOD_LEXICON}
        for c in inside:
            for k, v in c["characters"].items():
                chars[k] = chars.get(k, 0) + v
            if c["location"]:
                loc_time[c["location"]] = loc_time.get(c["location"], 0) + (c["end"] - c["start"])
            for m, v in c["moods"].items():
                moods[m] += v
        location = max(loc_time, key=loc_time.get) if loc_time else (scenes[-1]["location_id"] if scenes else None)
        mood = max(moods, key=lambda m: (moods[m], m == "mysterious")) if any(moods.values()) else "mysterious"
        intensity = sum(moods[m] * INTENSITY_WEIGHT[m] for m in moods) / max(end - start, 1) * 60
        motion = MOOD_MOTION[mood]
        if motion == prev_motion:
            motion = MOTIONS[(MOTIONS.index(motion) + 1) % len(MOTIONS)]
        prev_motion = motion
        text = " ".join(c["text"] for c in inside)
        scenes.append({
            "scene_no": idx,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "chunk_range": [inside[0]["chunk_no"], inside[-1]["chunk_no"]] if inside else None,
            "summary": text[:240] + ("…" if len(text) > 240 else ""),
            "characters": [k for k, _ in sorted(chars.items(), key=lambda kv: -kv[1])][:3],
            "location_id": location,
            "mood": mood,
            "intensity": round(intensity, 3),
            "motion": motion,
        })
    validate_coverage(scenes, total)
    return scenes


def validate_coverage(scenes: list[dict], total: float, tol: float = 1e-3) -> None:
    if not scenes:
        raise ValueError("no scenes")
    if abs(scenes[0]["start"]) > tol or abs(scenes[-1]["end"] - total) > tol:
        raise ValueError("scenes do not cover the full narration")
    for a, b in zip(scenes, scenes[1:]):
        if abs(a["end"] - b["start"]) > tol:
            raise ValueError(f"gap/overlap between scene {a['scene_no']} and {b['scene_no']}")
    for s in scenes:
        if s["end"] <= s["start"]:
            raise ValueError(f"scene {s['scene_no']} has non-positive duration")


def accelerate_scenes(scenes: list[dict], transcript: dict, fast_seconds: float) -> list[dict]:
    """Keep regular scene rules; split cold-open/intense/final shots at actual chunk boundaries."""
    total = float(transcript['duration_sec'])
    candidates = sorted({float(ch['start']) for ch in transcript['chunks']})
    result = []
    for scene in scenes:
        fast = scene['start'] < total*.03 or scene['end'] > total*.875 or scene.get('intensity', 0) >= 4
        start = scene['start']
        while fast and scene['end'] - start > fast_seconds*1.6:
            target = start + fast_seconds
            options = [t for t in candidates if start+5 < t < scene['end']-5]
            boundary = min(options, key=lambda t: abs(t-target)) if options else target
            result.append({**scene, 'start': start, 'end': boundary, 'duration': round(boundary-start,3)})
            start = boundary
        result.append({**scene, 'start': start, 'duration': round(scene['end']-start,3)})
    for number, scene in enumerate(result,1):
        scene['scene_no'] = number
    validate_coverage(result,total)
    return result
