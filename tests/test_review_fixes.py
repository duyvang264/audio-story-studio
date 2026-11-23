"""Regression tests for the issues found in the project review."""
import json
import time

import httpx
import pytest

from studio import jobs, pipeline, worker
from studio.db import connect
from studio.telegram import TelegramClient, TelegramError

pytestmark = pytest.mark.db


def _episode(status="created"):
    with connect() as c:
        st = c.execute("""INSERT INTO stories (slug, title, source_path, rights_type, rights_status)
                          VALUES (%s,'R','x','original','approved') RETURNING *""", (f"rf-{time.time_ns()}",)).fetchone()
        return c.execute("""INSERT INTO episodes (story_id, episode_no, title, chapter_from, chapter_to, status)
                            VALUES (%s,1,'R',1,1,%s) RETURNING *""", (st["id"], status)).fetchone()


def _drain_type(job_type, s):
    """Run queued jobs of one type only (other queued work is left alone)."""
    with connect() as c:
        c.execute("UPDATE jobs SET priority=1 WHERE type=%s AND status='queued'", (job_type,))
    return worker.run_one(s)


def test_cancel_stops_running_job_from_chaining(db, settings, monkeypatch):
    ep = _episode()
    job = jobs.enqueue("timeline", ep["id"], idempotency_key=f"tl-cancel-{ep['id']}", priority=1)

    def handler(j, s):
        jobs.cancel_episode_jobs(ep["id"])  # user sends /cancel while this step runs
        return {"ok": True}
    monkeypatch.setitem(pipeline.HANDLERS, "timeline", handler)
    assert worker.run_one(settings)
    with connect() as c:
        st = c.execute("SELECT status FROM jobs WHERE id=%s", (job["id"],)).fetchone()["status"]
        chained = c.execute("SELECT COUNT(*) AS n FROM jobs WHERE episode_id=%s AND type='render_preview'",
                            (ep["id"],)).fetchone()["n"]
        e = c.execute("SELECT status FROM episodes WHERE id=%s", (ep["id"],)).fetchone()
    assert (st, chained, e["status"]) == ("cancelled", 0, "cancelled")


def test_dead_worker_job_fails_after_max_attempts(db, settings):
    ep = _episode()
    job = jobs.enqueue("timeline", ep["id"], idempotency_key=f"tl-dead-{ep['id']}", max_attempts=1)
    with connect() as c:
        c.execute("""UPDATE jobs SET status='running', attempts=1, locked_by='ghost',
                     locked_at=now() - interval '1 hour' WHERE id=%s""", (job["id"],))
    jobs.claim("w")
    with connect() as c:
        row = c.execute("SELECT status, last_error FROM jobs WHERE id=%s", (job["id"],)).fetchone()
    assert row["status"] == "failed" and "worker lost" in row["last_error"]


def test_heartbeat_keeps_long_job_locked(db, settings):
    ep = _episode()
    job = jobs.enqueue("timeline", ep["id"], idempotency_key=f"tl-hb-{ep['id']}")
    with connect() as c:
        c.execute("""UPDATE jobs SET status='running', attempts=1, locked_by='w',
                     locked_at=now() - interval '1 hour' WHERE id=%s""", (job["id"],))
    jobs.heartbeat(job["id"])
    jobs.claim("w2")
    with connect() as c:
        assert c.execute("SELECT status FROM jobs WHERE id=%s", (job["id"],)).fetchone()["status"] == "running"
        c.execute("UPDATE jobs SET status='cancelled' WHERE id=%s", (job["id"],))


def test_worker_survives_notification_failure(db, settings, monkeypatch):
    ep = _episode()
    jobs.enqueue("timeline", ep["id"], idempotency_key=f"tl-notify-{ep['id']}", max_attempts=1, priority=1)
    monkeypatch.setitem(pipeline.HANDLERS, "timeline", lambda j, s: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(pipeline, "notify", lambda *a, **k: (_ for _ in ()).throw(TelegramError("down")))
    assert worker.run_one(settings)  # must not raise


def test_get_updates_network_error_is_telegram_error(settings, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(settings, "telegram_dry_run", False)

    def boom(*a, **k):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(TelegramError):
        TelegramClient(settings).get_updates(None, timeout=1)


def _final(ep_id, settings, passed=True):
    out = pipeline._final_path(settings, ep_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"x")
    d = settings.storage / "episodes" / str(ep_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"qc_report_{settings.final_height}p.json").write_text(json.dumps({"passed": passed}))
    with connect() as c:
        c.execute("UPDATE episodes SET final_path=%s WHERE id=%s", (str(out), ep_id))


def test_publish_queue_refuses_final_that_failed_qc(db, settings):
    ep = _episode()
    _final(ep["id"], settings, passed=False)
    with pytest.raises(pipeline.QCFailed):
        pipeline.h_publish_queue({"id": 0, "episode_id": ep["id"], "payload": {}}, settings)


def test_republish_after_reject_attaches_new_approval(db, settings, monkeypatch):
    # This is an approval state-machine test; real DSP/provenance gates have dedicated tests.
    monkeypatch.setattr(pipeline.audio_production, 'assert_publishable', lambda *a, **k: {'status': 'PASS'})
    ep = _episode()
    _final(ep["id"], settings)
    job = {"id": 0, "episode_id": ep["id"], "payload": {}}
    first = pipeline.h_publish_queue(job, settings)
    pipeline.decide(first["approval_id"], False, 111)
    second = pipeline.h_publish_queue(job, settings)
    assert second["approval_id"] != first["approval_id"]
    pipeline.decide(second["approval_id"], True, 111)
    with connect() as c:
        rec = c.execute("SELECT * FROM publishing_records WHERE episode_id=%s AND platform='youtube'",
                        (ep["id"],)).fetchone()
    assert rec["approval_id"] == second["approval_id"] and rec["status"] == "ready_manual"


def test_interrupted_upload_is_not_restarted(db, settings, monkeypatch):
    monkeypatch.setattr(pipeline.audio_production, 'assert_publishable', lambda *a, **k: {'status': 'PASS'})
    ep = _episode()
    _final(ep["id"], settings)
    res = pipeline.h_publish_queue({"id": 0, "episode_id": ep["id"], "payload": {}}, settings)
    pipeline.decide(res["approval_id"], True, 111)
    with connect() as c:
        c.execute("UPDATE publishing_records SET status='uploading' WHERE id=%s", (res["publishing_record"],))
    monkeypatch.setattr(settings, "publish_upload_enabled", True)
    called = []
    monkeypatch.setattr(pipeline.uploaders, "upload_record", lambda *a, **k: called.append(1))
    job = {"id": 0, "episode_id": ep["id"], "type": "publish_upload",
           "payload": {"record_id": res["publishing_record"]}}
    with pytest.raises(pipeline.UploadRefused):
        pipeline.h_publish_upload(job, settings)
    with connect() as c:
        st = c.execute("SELECT status FROM publishing_records WHERE id=%s", (res["publishing_record"],)).fetchone()
    assert not called and st["status"] == "failed"


def test_tiktok_failure_still_queues_youtube_publish(db, settings):
    ep = _episode()
    job = {"id": 999, "episode_id": ep["id"], "type": "qc_tiktok", "payload": {"run": "rx"}}
    pipeline.after_failure(job, "QCFailed: loudness", True, settings)
    with connect() as c:
        q = c.execute("SELECT * FROM jobs WHERE episode_id=%s AND type='publish_queue'", (ep["id"],)).fetchone()
        e = c.execute("SELECT status FROM episodes WHERE id=%s", (ep["id"],)).fetchone()
        c.execute("UPDATE jobs SET status='cancelled' WHERE id=%s", (q["id"],))
    assert q is not None and e["status"] != "failed"


def test_resend_preview_of_approved_episode_does_not_reopen_approval(db, settings):
    ep = _episode(status="ready_to_publish")
    d = settings.storage / "episodes" / str(ep["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "timeline.json").write_text(json.dumps({"duration_sec": 10, "scenes": []}))
    prev = pipeline._preview_path(settings, ep["id"])
    prev.parent.mkdir(parents=True, exist_ok=True)
    prev.write_bytes(b"x")
    res = pipeline.h_notify_preview({"id": 0, "episode_id": ep["id"], "payload": {"resend": True}}, settings)
    with connect() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM approvals WHERE episode_id=%s AND kind='content'",
                      (ep["id"],)).fetchone()["n"]
    assert res["resent_only"] and n == 0


def test_story_pasted_in_several_messages(db, settings):
    from studio import commands

    def msg(uid, text):
        return commands.handle_update({"update_id": uid, "message": {"from": {"id": 111}, "chat": {"id": 111},
                                                                      "text": text}}, settings)
    base = int(time.time() * 1000)
    msg(base, "/newstory")
    assert "Đã nhận" in msg(base + 1, "Bóng Ma Trên Đèo\n\n## Chương 1\n\n" + "Gió rít qua đèo đá. " * 8)["reply"]
    msg(base + 2, "## Chương 2\n\n" + "Ánh đuốc lập loè trong sương. " * 8)
    reply = msg(base + 3, "/xong")["reply"]
    assert "Bóng Ma Trên Đèo" in reply and "/approve rights" in reply
    with connect() as c:
        st = c.execute("SELECT * FROM stories WHERE title='Bóng Ma Trên Đèo' ORDER BY id DESC LIMIT 1").fetchone()
        n = c.execute("SELECT COUNT(*) AS n FROM chapters WHERE story_id=%s", (st["id"],)).fetchone()["n"]
    assert n == 2
    assert "Chưa có nội dung" in msg(base + 4, "/xong")["reply"]
