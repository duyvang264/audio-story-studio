"""Uploaders against mocked HTTP (no real Google/TikTok calls) and the approval gate around them."""
import json
import time

import httpx
import pytest

from studio import jobs, pipeline, uploaders
from studio.db import connect


@pytest.fixture
def secrets_env(settings, tmp_path, monkeypatch):
    cs = tmp_path / "client.json"
    cs.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}))
    monkeypatch.setattr(settings, "youtube_client_secrets", str(cs))
    monkeypatch.setattr(settings, "tiktok_client_key", "tk")
    monkeypatch.setattr(settings, "tiktok_client_secret", "ts")
    for platform in ("youtube", "tiktok"):
        uploaders.save_token(platform, {"access_token": "old", "refresh_token": "r", "expires_at": 0}, settings)
    yield settings
    for platform in ("youtube", "tiktok"):
        uploaders.token_path(platform, settings).unlink(missing_ok=True)


def _video(tmp_path, size):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"\0" * size)
    return v


def test_token_file_is_private(secrets_env):
    assert uploaders.token_path("youtube", secrets_env).stat().st_mode & 0o777 == 0o600


def test_youtube_resumable_upload_refreshes_token_and_resumes(secrets_env, tmp_path, monkeypatch):
    monkeypatch.setattr(uploaders, "CHUNK", 10)
    video, srt = _video(tmp_path, 25), tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", encoding="utf-8")
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.host + req.url.path, req.headers.get("content-range")))
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer new"
        if req.method == "POST" and req.url.path.endswith("/videos"):
            body = json.loads(req.content)
            assert body["status"]["privacyStatus"] == "private"
            return httpx.Response(200, headers={"location": "https://upload.example/session"})
        if req.method == "PUT":
            rng = req.headers["content-range"]
            if rng.startswith("bytes 0-"):
                return httpx.Response(308, headers={"range": "bytes=0-9"})
            if rng.startswith("bytes 10-"):
                return httpx.Response(308, headers={"range": "bytes=0-19"})
            return httpx.Response(200, json={"id": "abc123"})
        if req.url.path.endswith("/captions"):
            assert b"Xin ch" in req.content
            return httpx.Response(200, json={"id": "cap"})
        return httpx.Response(500)

    rec = {"platform": "youtube", "video_path": str(video), "title": "T", "description": "D", "tags": ["x"]}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        res = uploaders.upload_record(rec, secrets_env, client, srt=srt)
    assert res == {"external_id": "abc123", "url": "https://youtu.be/abc123", "privacy": "private", "captions": "ok"}
    assert [c[2] for c in calls if c[0] == "PUT"] == ["bytes 0-9/25", "bytes 10-19/25", "bytes 20-24/25"]
    assert uploaders.load_token("youtube", secrets_env)["access_token"] == "new"


def test_tiktok_inbox_upload(secrets_env, tmp_path):
    video = _video(tmp_path, 1000)
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.path)
        if req.url.path == "/v2/oauth/token/":
            assert b"grant_type=refresh_token" in req.content
            return httpx.Response(200, json={"access_token": "tt", "refresh_token": "r2", "expires_in": 86400})
        if req.url.path.endswith("/inbox/video/init/"):
            info = json.loads(req.content)["source_info"]
            assert info == {"source": "FILE_UPLOAD", "video_size": 1000, "chunk_size": 1000, "total_chunk_count": 1}
            return httpx.Response(200, json={"data": {"upload_url": "https://up.example/x", "publish_id": "p1"},
                                             "error": {"code": "ok"}})
        if req.method == "PUT":
            assert req.headers["content-range"] == "bytes 0-999/1000"
            return httpx.Response(201)
        if req.url.path.endswith("/status/fetch/"):
            return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}})
        return httpx.Response(500)

    rec = {"platform": "tiktok", "video_path": str(video), "title": "T"}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        res = uploaders.upload_record(rec, secrets_env, client)
    assert res == {"external_id": "p1", "status": "SEND_TO_USER_INBOX", "mode": "inbox_draft"}
    assert uploaders.load_token("tiktok", secrets_env)["refresh_token"] == "r2"


def _publish_fixture(tmp_path):
    with connect() as c:
        st = c.execute("""INSERT INTO stories (slug, title, source_path, rights_type, rights_status)
                          VALUES (%s,'U','x','original','approved') RETURNING *""",
                       (f"upl-{time.time_ns()}",)).fetchone()
        ep = c.execute("""INSERT INTO episodes (story_id, episode_no, title, chapter_from, chapter_to) VALUES (%s,1,'U',1,1) RETURNING *""",
                       (st["id"],)).fetchone()
        ap = c.execute("INSERT INTO approvals (kind, episode_id) VALUES ('publish',%s) RETURNING *",
                       (ep["id"],)).fetchone()
        recs = [c.execute("""INSERT INTO publishing_records (episode_id, platform, video_path, title, approval_id)
                             VALUES (%s,%s,%s,'U',%s) RETURNING *""",
                          (ep["id"], pf, str(_video(tmp_path, 10)), ap["id"])).fetchone()
                for pf in ("youtube", "tiktok")]
    return ep, ap, recs


def test_upload_requires_human_approval_and_enable_flag(db, secrets_env, tmp_path, monkeypatch):
    # Isolate auth/enable behavior; missing/stale audio evidence is tested separately.
    monkeypatch.setattr(pipeline.audio_production, 'assert_publishable', lambda *a, **k: {'status': 'PASS'})
    ep, ap, recs = _publish_fixture(tmp_path)
    job = {"id": 0, "episode_id": ep["id"], "type": "publish_upload", "payload": {"record_id": recs[0]["id"]}}
    with pytest.raises(pipeline.UploadRefused):  # not approved yet
        pipeline.h_publish_upload(job, secrets_env)

    monkeypatch.setattr(secrets_env, "publish_upload_enabled", False)
    pipeline.decide(ap["id"], True, 111)
    with connect() as c:
        statuses = {r["platform"]: r["status"] for r in
                    c.execute("SELECT * FROM publishing_records WHERE approval_id=%s", (ap["id"],)).fetchall()}
        queued = c.execute("SELECT COUNT(*) AS n FROM jobs WHERE type='publish_upload' AND episode_id=%s",
                           (ep["id"],)).fetchone()["n"]
    assert statuses == {"youtube": "ready_manual", "tiktok": "ready_manual"} and queued == 0


def test_approved_publish_enqueues_and_uploads(db, secrets_env, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.audio_production, 'assert_publishable', lambda *a, **k: {'status': 'PASS'})
    monkeypatch.setattr(secrets_env, "publish_upload_enabled", True)
    uploaders.token_path("tiktok", secrets_env).unlink()  # only YouTube is authorised
    ep, ap, recs = _publish_fixture(tmp_path)
    pipeline.decide(ap["id"], True, 111)
    with connect() as c:
        statuses = {r["platform"]: r["status"] for r in
                    c.execute("SELECT * FROM publishing_records WHERE approval_id=%s", (ap["id"],)).fetchall()}
        job = c.execute("SELECT * FROM jobs WHERE type='publish_upload' AND episode_id=%s", (ep["id"],)).fetchone()
    assert statuses == {"youtube": "approved", "tiktok": "ready_manual"}
    assert job["payload"]["record_id"] == recs[0]["id"]

    monkeypatch.setattr(uploaders, "upload_record",
                        lambda rec, s, srt=None: {"external_id": "vid1", "url": "https://youtu.be/vid1",
                                                  "privacy": "private"})
    res = pipeline.h_publish_upload(job, secrets_env)
    assert res["external_id"] == "vid1"
    with connect() as c:
        rec = c.execute("SELECT * FROM publishing_records WHERE id=%s", (recs[0]["id"],)).fetchone()
        c.execute("UPDATE jobs SET status='cancelled' WHERE id=%s", (job["id"],))
    assert (rec["status"], rec["external_url"]) == ("published", "https://youtu.be/vid1")
    # idempotent: a second run does not upload again
    assert pipeline.h_publish_upload(job, secrets_env)["already_uploaded"]
    assert jobs.next_stage("publish_upload") is None


def test_publish_approval_cannot_bypass_missing_audio_qc(db, settings, tmp_path):
    ep, ap, recs = _publish_fixture(tmp_path)
    with pytest.raises(pipeline.ApprovalError, match='Mandatory audio stage'):
        pipeline.decide(ap['id'], True, 111)
    with connect() as c:
        assert c.execute('SELECT status FROM approvals WHERE id=%s', (ap['id'],)).fetchone()['status'] == 'pending'
        assert c.execute("SELECT COUNT(*) AS n FROM jobs WHERE type='publish_upload' AND episode_id=%s", (ep['id'],)).fetchone()['n'] == 0
