"""Tests needing PostgreSQL and/or ffmpeg: job queue, budget, Telegram controller, render engine."""
from pathlib import Path

import pytest

from studio import budget, commands, jobs, media
from studio.db import connect
from studio.render import motion_exprs, render_clip
from studio.visual.providers import MockImageProvider

pytestmark = pytest.mark.db


def _update(uid, text, user=111):
    return {"update_id": uid, "message": {"from": {"id": user}, "chat": {"id": user}, "text": text}}


def test_enqueue_is_idempotent(db):
    a = jobs.enqueue("tts", None, idempotency_key="k-idem")
    b = jobs.enqueue("tts", None, idempotency_key="k-idem")
    assert a["id"] == b["id"] and a["created"] and not b["created"]
    with pytest.raises(ValueError):
        jobs.enqueue("rm -rf", None)
    with connect() as c:
        c.execute("DELETE FROM jobs WHERE idempotency_key='k-idem'")


def test_claim_retry_then_fail(db):
    job = jobs.enqueue("timeline", None, idempotency_key="k-retry", max_attempts=2)
    claimed = jobs.claim("w1")
    assert claimed["id"] == job["id"] and claimed["status"] == "running"
    assert jobs.claim("w2") is None  # locked / nothing else runnable
    assert jobs.fail(claimed, "boom") == "queued"
    with connect() as c:
        c.execute("UPDATE jobs SET run_after=now() WHERE id=%s", (job["id"],))
    again = jobs.claim("w1")
    assert again["attempts"] == 2
    assert jobs.fail(again, "boom2") == "failed"
    with connect() as c:
        c.execute("DELETE FROM jobs WHERE id=%s", (job["id"],))


def test_budget_blocks_paid_and_unknown_providers(db, settings):
    with pytest.raises(budget.BudgetError):
        budget.reserve("image", "openai_api", 1, settings=settings)
    r = budget.reserve("image", "mock", 1, settings=settings)
    assert r.estimated_vnd == 0
    budget.commit(r)
    assert budget.summary(settings)["allow_paid_api"] is False


def test_budget_limits_enforced_for_priced_provider(db, settings, monkeypatch):
    monkeypatch.setitem(budget.PROVIDER_PRICING, ("image", "fake_paid"), (150_000, True))
    monkeypatch.setattr(settings, "allow_paid_api", True)
    first = budget.reserve("image", "fake_paid", 1, settings=settings)
    with pytest.raises(budget.BudgetError):
        budget.reserve("image", "fake_paid", 1, settings=settings)  # 300k > IMAGE_BUDGET 200k
    budget.release(first)


def test_telegram_rejects_unauthorised_and_duplicates(db, settings):
    assert commands.handle_update(_update(1, "/start", user=666), settings)["reason"] == "unauthorized"
    assert commands.handle_update(_update(2, "/start"), settings)["ok"]
    assert commands.handle_update(_update(2, "/start"), settings).get("duplicate")


def test_telegram_command_args_are_validated(db, settings):
    res = commands.handle_update(_update(3, "/create_episode 1;rm -rf /"), settings)
    assert res["reply"].startswith("⚠️")
    res = commands.handle_update(_update(4, "/regen_scene abc 1"), settings)
    assert "Cú pháp" in res["reply"]
    res = commands.handle_update(_update(5, "/unknown"), settings)
    assert res["reply"].startswith("⚠️")


def test_create_episode_requires_rights_approval(db, settings):
    commands.handle_update(_update(6, "/newstory sample"), settings)
    with connect() as c:
        st = c.execute("SELECT * FROM stories ORDER BY id DESC LIMIT 1").fetchone()
    res = commands.handle_update(_update(7, f"/create_episode {st['id']}"), settings)
    assert "rights not approved" in res["reply"]


@pytest.mark.media
@pytest.mark.parametrize("motion", ["slow_zoom_in", "slow_zoom_out", "pan_left_to_right", "pan_right_to_left"])
def test_render_clip_motion_and_cache(tmp_path, settings, motion):
    img = tmp_path / "a.png"
    MockImageProvider(settings).generate("test prompt", "", img, mood="dread")
    clip, hit = render_clip(img, motion, 12, 320, 180, 25, 30, tmp_path / "clips")
    assert not hit and media.video_frame_count(clip) == 12
    p = media.probe(clip)["streams"][0]
    assert (p["width"], p["height"]) == (320, 180)
    _, hit2 = render_clip(img, motion, 12, 320, 180, 25, 30, tmp_path / "clips")
    assert hit2


def test_register_asset_reactivates_retired_duplicate(db, settings, tmp_path):
    from studio.visual.engine import register_asset
    img = tmp_path / "x.png"
    MockImageProvider(settings).generate("dup prompt", "", img)
    story = {"id": None, "slug": "dup-test"}
    kw = dict(src=img, story=story, kind="scene", provider="mock", prompt="p", style_profile="s",
              character_ids=[], location_id="l", mood="calm", tags=[], storage=settings.storage)
    with connect() as c:
        a = register_asset(c, **kw)
        c.execute("UPDATE visual_assets SET status='retired' WHERE id=%s", (a["id"],))
        b = register_asset(c, **kw)
    assert b["id"] == a["id"] and b["status"] == "active"


def test_unknown_motion_rejected():
    with pytest.raises(ValueError):
        motion_exprs("spin", 10)


def test_mock_assets_not_reused_by_real_providers(db, settings, tmp_path):
    from studio.visual.engine import _assets, register_asset
    img = tmp_path / "m.png"
    MockImageProvider(settings).generate("mock-only prompt", "", img)
    kw = dict(src=img, story={"id": None, "slug": "mock-filter"}, kind="scene", provider="mock", prompt="p",
              style_profile="style-mock-filter", character_ids=[], location_id="l", mood="calm", tags=[],
              storage=settings.storage)
    with connect() as c:
        a = register_asset(c, **kw)
        assert a["id"] in {r["id"] for r in _assets(c, "style-mock-filter", "mock")}
        assert _assets(c, "style-mock-filter", "codex") == []


def test_local_tts_explicit_regeneration_keeps_original_sources(db,settings):
    import json,time
    from studio.story import import_story,create_episodes,build_episode_script
    from studio.audio import produce_narration
    from studio.audio_engine.io import digest
    path=settings.storage/'stories'/f'regen-{time.time_ns()}.md'
    path.write_text('---\ntitle: Regeneration test\nrights: original\n---\n\n## Một\n\nCánh cửa cũ mở ra. Tôi nghe tiếng gió.\n')
    st=import_story(path)['story']
    with connect() as c:c.execute("UPDATE stories SET rights_status='approved' WHERE id=%s",(st['id'],))
    ep=create_episodes(st['id'])[0];build_episode_script(ep['id'])
    first=produce_narration(ep['id'],settings=settings)
    before=digest(first['audio']);tr=settings.storage/'episodes'/str(ep['id'])/'transcript_timeline.json'
    old=json.loads(tr.read_text())
    second=produce_narration(ep['id'],settings=settings,regenerate_chunks=[1],regeneration_key='take-two')
    new=json.loads(tr.read_text())
    assert old['chunks'][0]['audio_path']!=new['chunks'][0]['audio_path']
    assert old['chunks'][1]['audio_path']==new['chunks'][1]['audio_path']
    assert digest(first['audio'])==before and first['audio']!=second['audio']


def test_import_preserves_continuity_and_audio_metadata(db,settings):
    import time,json
    from studio.story import import_story,create_episodes,build_episode_script
    p=settings.storage/'stories'/f'canon-{time.time_ns()}.md'
    p.write_text('---\ntitle: Canon metadata test\nrights: original\nseries: Canon series\ncontinuity:\n  grandmother: dead\nvoice_cast:\n  narrator: Thiện Minh\nsound_cues: []\n---\n\n## Chương một\n\nBà đã chết. Nhưng tiếng gọi vẫn còn.\n')
    st=import_story(p)['story']
    assert st['metadata']['continuity']=={'grandmother':'dead'}
    with connect() as c:c.execute("UPDATE stories SET rights_status='approved' WHERE id=%s",(st['id'],))
    ep=create_episodes(st['id'])[0];result=build_episode_script(ep['id']);script=json.loads(Path(result['path']).read_text())
    assert script['series']=='Canon series' and script['voice_cast']['narrator']=='Thiện Minh'
    assert script['continuity']['grandmother']=='dead' and script['sound_cues']==[]


def test_telegram_local_story_generation_is_async_idempotent_and_rights_pending(db,settings,monkeypatch):
    import time
    from studio import horror_story,worker
    calls=[]
    def generate(brief,cfg,continuity):
        calls.append(brief)
        script={'title':'Generated local horror','continuity':{'bell':'inaudible'},'sections':[{'section_no':1,'chapter_title':'Mở đầu',
                'paragraphs':[{'paragraph_no':1,'text':'Bà đã chết. Nhưng có tiếng gọi dưới giếng.','sentences':['Bà đã chết.','Nhưng có tiếng gọi dưới giếng.']}]}]}
        return {'script':script,'quality':{'status':'PASS'},'provider':'local-only','paid_api':False}
    monkeypatch.setattr(horror_story,'generate',generate)
    base=int(time.time()*1000)
    response=commands.handle_update(_update(base,'/generate_story Một lá thư từ người bà đã mất'))
    assert 'job #' in response['reply']
    with connect() as c:
        job=c.execute("SELECT * FROM jobs WHERE type='story_generate' AND status='queued' ORDER BY id DESC LIMIT 1").fetchone()
        c.execute('UPDATE jobs SET priority=0 WHERE id=%s',(job['id'],))
    assert job['episode_id'] is None and job['max_attempts']==2
    worker.run_one(settings)
    with connect() as c:
        done=c.execute('SELECT * FROM jobs WHERE id=%s',(job['id'],)).fetchone()
        story=c.execute('SELECT * FROM stories WHERE id=%s',(done['result']['story_id'],)).fetchone()
    assert done['status']=='succeeded' and story['rights_status']=='pending'
    commands.handle_update(_update(base+1,'/generate_story Một lá thư từ người bà đã mất'))
    assert len(calls)==1
