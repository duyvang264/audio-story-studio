"""Integration: a tiny story through the whole pipeline (tone TTS, mock images, low resolution)."""
import json
from pathlib import Path

import pytest

from studio import commands, worker
from studio.db import connect

pytestmark = [pytest.mark.db, pytest.mark.media, pytest.mark.slow]

STORY = """---
title: Bóng Đêm Thử Nghiệm
rights: original
characters:
  - {id: an, name: An, visual: "a young knight"}
  - {id: binh, name: Bình, visual: "an old monk"}
locations:
  - {id: rung, name: Rừng Đen, visual: "a dead forest"}
  - {id: thap, name: Tháp Cổ, visual: "an ancient tower"}
---

## Chương 1

An bước vào Rừng Đen khi sương xuống. Gió lạnh thổi qua những thân cây chết.

An nghe tiếng thì thầm trong bóng tối. Cậu rùng mình nhưng vẫn đi tiếp.

***

## Chương 2

Bình đợi An ở chân Tháp Cổ. Ông lão cầm một ngọn đèn nhỏ.

Hai người leo lên tháp. Ánh sáng bình minh dần hiện ra phía chân trời.
"""


def _tg(uid, text):
    return commands.handle_update({"update_id": uid, "message": {"from": {"id": 111}, "chat": {"id": 111},
                                                                 "text": text}})


def _drain(s):
    while worker.run_one(s):
        pass


def test_full_pipeline_preview_regen_final(db, settings, tmp_path):
    src = settings.storage / "stories" / "thu-nghiem.md"
    src.write_text(STORY, encoding="utf-8")
    from studio.story import import_story
    st = import_story(src)["story"]
    _tg(1001, f"/approve rights {st['id']}")
    _tg(1002, f"/create_episode {st['id']}")
    _drain(settings)

    with connect() as c:
        ep = c.execute("SELECT * FROM episodes WHERE story_id=%s", (st["id"],)).fetchone()
        failed = c.execute("SELECT type, last_error FROM jobs WHERE episode_id=%s AND status='failed'",
                           (ep["id"],)).fetchall()
        scenes = c.execute("SELECT * FROM scenes WHERE episode_id=%s ORDER BY scene_no", (ep["id"],)).fetchall()
    assert not failed, failed
    assert ep["status"] == "awaiting_approval"
    assert len(scenes) >= 2
    assert {s["location_id"] for s in scenes} == {"rung", "thap"}

    d = settings.storage / "episodes" / str(ep["id"])
    for name in ("episode_script.json", "transcript_timeline.json", "scene_plan.json",
                 "image_generation_queue.json", "scene_assets_map.json", "timeline.json", "subtitles.srt"):
        assert (d / name).exists(), name
    qc = json.loads((d / "qc_report_180p.json").read_text())
    assert qc["passed"], qc["issues"]
    assert Path(ep["preview_path"]).exists()

    # Regenerate one scene: only that clip is re-rendered.
    _tg(1003, f"/regen_scene {ep['id']} 1")
    _drain(settings)
    with connect() as c:
        r = c.execute("""SELECT result FROM jobs WHERE episode_id=%s AND type='render_preview' AND status='succeeded'
                         ORDER BY id DESC LIMIT 1""", (ep["id"],)).fetchone()["result"]
    assert r["clips_reused"] == len(scenes) - 1

    # Approve -> final render, QC, publish queue (awaiting approval, never auto-published).
    _tg(1004, f"/approve {ep['id']}")
    _drain(settings)
    with connect() as c:
        ep = c.execute("SELECT * FROM episodes WHERE id=%s", (ep["id"],)).fetchone()
        pubs = {r["platform"]: r for r in c.execute("SELECT * FROM publishing_records WHERE episode_id=%s",
                                                       (ep["id"],)).fetchall()}
    assert ep["status"] == "ready_to_publish"
    assert json.loads((d / "qc_report_360p.json").read_text())["passed"]
    assert set(pubs) == {"youtube", "tiktok"}
    assert all(p["status"] == "awaiting_approval" for p in pubs.values())
    assert pubs["tiktok"]["approval_id"] == pubs["youtube"]["approval_id"]

    # TikTok cut: 9:16, whole paragraphs inside the configured bounds, QC passed.
    qc_short = json.loads((d / "qc_report_tiktok.json").read_text())
    assert qc_short["passed"], qc_short["issues"]
    meta = json.loads((d / "tiktok.json").read_text())
    assert qc_short["info"]["resolution"] == "180x320"
    if meta["fits_bounds"]:
        assert settings.tiktok_min_sec <= meta["duration_sec"] <= settings.tiktok_max_sec + 1

    outbox = (settings.storage / "logs" / "telegram_outbox.jsonl").read_text().splitlines()
    assert sum('"sendVideo"' in line for line in outbox) >= 2
