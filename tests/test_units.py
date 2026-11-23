"""Pure unit tests: script building, scene planning, matching, subtitles."""
import pytest

from studio.commands import SAMPLE_STORY
from studio.story import find_mentions, normalize_narration, parse_story_file, slugify, split_sentences
from studio.timeline import build_cues, scene_frames, srt_time
from studio.visual import matcher
from studio.visual.prompts import build_prompt
from studio.visual.scene_planner import SceneRules, choose_boundaries, plan_scenes, validate_coverage


def test_slugify_vietnamese():
    assert slugify("Ngọn Đèn Cuối Cùng Của Vực Tro") == "ngon-den-cuoi-cung-cua-vuc-tro"


def test_parse_sample_story():
    parsed = parse_story_file(SAMPLE_STORY)
    assert parsed["meta"]["rights"] == "original"
    assert len(parsed["chapters"]) == 4
    assert {c["id"] for c in parsed["meta"]["characters"]} == {"lam_phong", "tieu_van", "lao_giu_tro"}


def test_split_sentences_and_long_sentence():
    assert split_sentences("Một. Hai! Ba?") == ["Một.", "Hai!", "Ba?"]
    long = ("chữ " * 120).strip() + "."
    parts = split_sentences(long)
    assert all(len(p) <= 220 for p in parts) and "".join(parts).replace(" ", "") == long.replace(" ", "")


def test_normalize_strips_markdown_and_dashes():
    assert normalize_narration("**Đêm** — gió   lạnh...") == "Đêm, gió lạnh…"


def test_find_mentions_word_boundaries():
    entries = [{"id": "van", "names": ["vân"]}]
    assert find_mentions("Tiểu Vân đứng đó", entries) == {"van": 1}
    assert find_mentions("vânvân", entries) == {}


def _timeline(paras):
    """paras: list of (section_no, duration, text)."""
    t, chunks = 0.0, []
    for i, (sec, dur, text) in enumerate(paras, start=1):
        chunks.append({"chunk_no": i, "section_no": sec, "paragraph_no": i, "text": text,
                       "start": t, "end": t + dur, "pause_after": 0.6})
        t += dur + 0.6
    return {"duration_sec": round(t, 3), "chunks": chunks}


SCRIPT = {"characters": [{"id": "a", "name": "An"}], "locations": [
    {"id": "forest", "name": "Rừng Đen"}, {"id": "castle", "name": "Lâu Đài"}]}


def test_plan_scenes_respects_rules_and_covers_everything():
    paras = [(1, 30, "An đi vào Rừng Đen.")] * 4 + [(2, 30, "An tới Lâu Đài.")] * 5
    tl = _timeline(paras)
    rules = SceneRules(120, 45, 150)
    scenes = plan_scenes(SCRIPT, tl, rules)
    validate_coverage(scenes, tl["duration_sec"])
    assert all(rules.min <= s["duration"] <= rules.max for s in scenes)
    # The section/location change at paragraph 4->5 should be a boundary.
    assert any(abs(s["start"] - (tl["chunks"][3]["end"] + 0.3)) < 1e-6 for s in scenes)
    assert scenes[0]["location_id"] == "forest" and scenes[-1]["location_id"] == "castle"


def test_plan_scenes_short_narration_single_scene():
    tl = _timeline([(1, 20, "An.")])
    scenes = plan_scenes(SCRIPT, tl, SceneRules())
    assert len(scenes) == 1 and scenes[0]["end"] == tl["duration_sec"]


def test_plan_scenes_falls_back_to_sentence_boundaries():
    chunks, t = [], 0.0
    for i in range(10):  # one 300s paragraph made of 10 sentences
        chunks.append({"chunk_no": i + 1, "section_no": 1, "paragraph_no": 1, "text": "Câu.",
                       "start": t, "end": t + 29.75, "pause_after": 0.25})
        t += 30
    scenes = plan_scenes(SCRIPT, {"duration_sec": t, "chunks": chunks}, SceneRules())
    assert len(scenes) >= 2 and all(s["duration"] <= 150 for s in scenes)


def test_choose_boundaries_infeasible_returns_none():
    assert choose_boundaries(400, [(200, 0)], SceneRules(120, 45, 150)) is None


def test_matcher_never_uses_wrong_character_or_location():
    scene = {"characters": ["a"], "location_id": "forest", "mood": "dread"}
    good = {"id": 1, "style_profile": "p", "character_ids": ["a"], "location_id": "forest", "mood": "dread"}
    wrong_char = {"id": 2, "style_profile": "p", "character_ids": ["a", "b"], "location_id": "forest"}
    wrong_loc = {"id": 3, "style_profile": "p", "character_ids": ["a"], "location_id": "castle"}
    env = {"id": 4, "style_profile": "p", "character_ids": [], "location_id": "forest"}
    other_style = {"id": 5, "style_profile": "q", "character_ids": ["a"], "location_id": "forest"}
    assert not matcher.compatible(scene, wrong_char, "p")
    assert not matcher.compatible(scene, wrong_loc, "p")
    assert not matcher.compatible(scene, other_style, "p")
    asset, mtype, _ = matcher.best_match(scene, [wrong_char, wrong_loc, env, good, other_style], "p")
    assert asset["id"] == 1 and mtype == "exact"
    asset, mtype, _ = matcher.best_match(scene, [env], "p")
    assert asset["id"] == 4 and mtype == "compatible"


def test_prompt_contains_style_and_negative():
    script = {"characters": [{"id": "a", "name": "An", "visual": "a knight in black armor"}],
              "locations": [{"id": "forest", "name": "Rừng", "visual": "a dead forest"}]}
    prompt, neg = build_prompt(characters=["a"], location_id="forest", mood="dread", script=script,
                               style_profile="dark_fantasy_cinematic")
    assert "a knight in black armor" in prompt and "a dead forest" in prompt and "16:9" in prompt
    assert "watermark" in neg


def test_subtitle_cues_short_lines_and_contiguous():
    chunks = [{"text": "Một câu rất dài " * 12, "start": 1.0, "end": 11.0}]
    cues = build_cues(chunks)
    assert cues[0]["start"] == 1.0 and abs(cues[-1]["end"] - 11.0) < 1e-6
    assert all(len(line) <= 42 for c in cues for line in c["text"].splitlines())
    assert all(len(c["text"].splitlines()) <= 2 for c in cues)


def test_scene_frames_sum_exact():
    scenes = [{"start": 0, "end": 10.013}, {"start": 10.013, "end": 20.5}, {"start": 20.5, "end": 33.337}]
    assert sum(scene_frames(scenes, 25)) == round(33.337 * 25)


@pytest.mark.parametrize("t,expected", [(0, "00:00:00,000"), (3725.5, "01:02:05,500")])
def test_srt_time(t, expected):
    assert srt_time(t) == expected


def _chunks(spec):
    """spec: list of (paragraph_no, seconds); 0.3 s pause between chunks."""
    out, t = [], 0.6
    for n, (para, sec) in enumerate(spec, 1):
        out.append({"chunk_no": n, "section_no": 1, "paragraph_no": para, "text": f"Câu {n}.",
                    "start": t, "end": t + sec})
        t += sec + 0.3
    return out


def test_short_window_picks_intense_whole_paragraphs():
    from studio.shorts import select_window
    chunks = _chunks([(1, 20), (1, 20), (2, 25), (2, 25), (3, 30), (3, 30), (4, 20), (4, 20)])
    total = chunks[-1]["end"] + 1
    scenes = [{"scene_no": 1, "start": 0, "end": 92, "intensity": 0.5},
              {"scene_no": 2, "start": 92, "end": 153, "intensity": 3.0},
              {"scene_no": 3, "start": 153, "end": total, "intensity": 1.0}]
    w = select_window(chunks, scenes, min_sec=60, max_sec=120, target_sec=90, total_sec=total)
    assert (w["first_chunk"], w["last_chunk"]) == (5, 6)  # paragraph 3 = the battle scene
    assert w["fits_bounds"] and 60 <= w["end"] - w["start"] <= 121


def test_short_window_falls_back_when_story_too_short():
    from studio.shorts import select_window
    chunks = _chunks([(1, 5), (2, 6)])
    w = select_window(chunks, [], min_sec=60, max_sec=120, target_sec=90, total_sec=chunks[-1]["end"] + 1)
    assert (w["first_chunk"], w["last_chunk"]) == (1, 2) and not w["fits_bounds"]


def test_slice_scenes_frames_sum_and_merge_tiny_slices():
    from studio.shorts import slice_scenes
    scenes = [{"scene_no": 1, "start": 0, "end": 10.5}, {"scene_no": 2, "start": 10.5, "end": 40},
              {"scene_no": 3, "start": 40, "end": 41}, {"scene_no": 4, "start": 41, "end": 80}]
    segs = slice_scenes(scenes, 10.0, 70.0, 25)
    assert [sg["scene_no"] for sg in segs] == [2, 4]  # 0.5 s of scene 1 and 1 s of scene 3 are merged away
    assert sum(sg["frames"] for sg in segs) == round(60 * 25)
