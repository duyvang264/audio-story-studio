"""n8n workflow layer: approval gates on the stage endpoint, failure events, generated JSON in sync."""
import importlib.util
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import jobs, pipeline
from studio.api import app
from studio.db import connect

ROOT = Path(__file__).resolve().parents[1]
KEY = {"X-API-Key": "test-key"}


def _episode(rights="approved"):
    with connect() as c:
        st = c.execute("""INSERT INTO stories (slug, title, source_path, rights_type, rights_status)
                          VALUES (%s,'G','x','original',%s) RETURNING *""", (f"wg-{time.time_ns()}", rights)).fetchone()
        return c.execute("""INSERT INTO episodes (story_id, episode_no, title, chapter_from, chapter_to, status)
                            VALUES (%s,1,'G',1,1,'created') RETURNING *""", (st["id"],)).fetchone()


@pytest.fixture(scope="module")
def client(db):
    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------------ stage endpoint gates

@pytest.mark.db
@pytest.mark.parametrize("stage", sorted(jobs.ALL_JOB_TYPES - set(jobs.STAGES)))
def test_post_approval_stages_cannot_be_enqueued_via_api(client, stage):
    ep = _episode()
    r = client.post(f"/api/episodes/{ep['id']}/stages/{stage}", json={"run": "x"}, headers=KEY)
    assert r.status_code == 403
    with connect() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM jobs WHERE episode_id=%s", (ep["id"],)).fetchone()["n"] == 0


@pytest.mark.db
def test_stage_requires_rights_approval(client):
    ep = _episode(rights="pending")
    r = client.post(f"/api/episodes/{ep['id']}/stages/script_build", json={"run": "x"}, headers=KEY)
    assert r.status_code == 409


@pytest.mark.db
def test_stage_unknown_episode(client):
    r = client.post("/api/episodes/987654321/stages/script_build", json={"run": "x"}, headers=KEY)
    assert r.status_code == 404


@pytest.mark.db
def test_pre_approval_stage_is_enqueued_idempotently(client):
    ep = _episode()
    url = f"/api/episodes/{ep['id']}/stages/render_preview"
    first = client.post(url, json={"run": "r1"}, headers=KEY).json()
    again = client.post(url, json={"run": "r1"}, headers=KEY).json()
    assert first["created"] is True and again["job_id"] == first["job_id"] and again["created"] is False


# ------------------------------------------------------------------ render_final defense in depth

@pytest.mark.db
def test_render_final_refuses_without_content_approval(db, settings, monkeypatch):
    monkeypatch.setattr(pipeline.render, "render_episode", lambda *a, **k: pytest.fail("must not render"))
    ep = _episode()
    with pytest.raises(pipeline.ApprovalError) as exc:
        pipeline.h_render_final({"id": 0, "episode_id": ep["id"], "payload": {}}, settings)
    assert exc.value.retryable is False


@pytest.mark.db
def test_render_final_uses_latest_content_approval(db, settings, monkeypatch):
    monkeypatch.setattr(pipeline.render, "render_episode", lambda *a, **k: {"rendered": True})
    ep = _episode()
    job = {"id": 0, "episode_id": ep["id"], "payload": {}}
    old = pipeline.request_approval("content", episode_id=ep["id"])
    with connect() as c:
        c.execute("UPDATE approvals SET status='approved' WHERE id=%s", (old["id"],))
    assert pipeline.h_render_final(job, settings) == {"rendered": True}
    pipeline.request_approval("content", episode_id=ep["id"])  # a newer preview reopens review
    with pytest.raises(pipeline.ApprovalError):
        pipeline.h_render_final(job, settings)


# ------------------------------------------------------------------ events: only final failures

def test_only_final_failures_reach_n8n(settings, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "emit_event", lambda event, *a, **k: sent.append(event))
    monkeypatch.setattr(pipeline, "notify", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "set_stage", lambda *a, **k: None)
    job = {"id": 1, "type": "tts", "episode_id": 1, "payload": {}}
    monkeypatch.setattr(settings, "auto_chain", False)
    pipeline.after_success(job, settings)
    pipeline.after_failure(job, "boom", False, settings)
    assert sent == []
    pipeline.after_failure(job, "boom", True, settings)
    assert sent == ["stage.failed"]


def _generator():
    spec = importlib.util.spec_from_file_location("gen_n8n", ROOT / "scripts" / "gen_n8n_workflows.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_events_workflow_does_not_notify_telegram_again():
    wf = _generator().wf07()
    event_nodes = {n["name"] for n in wf["nodes"]} - {n["name"] for n in _generator().stage_workflow(
        "WF-07", "x", "ass/wf07-render", "render_preview", "")["nodes"]}
    urls = [n["parameters"].get("url", "") for n in wf["nodes"] if n["name"] in event_nodes]
    assert not any("/api/notify" in u for u in urls)
    assert wf["connections"]["Pipeline events"]["main"][0] == [{"node": "Ack event", "type": "main", "index": 0}]


def test_committed_workflows_match_generator(tmp_path, monkeypatch):
    gen = _generator()
    monkeypatch.setattr(gen, "OUT", tmp_path)
    gen.main()
    committed = sorted(p.name for p in (ROOT / "n8n" / "workflows").glob("*.json"))
    assert committed == sorted(p.name for p in tmp_path.glob("*.json"))
    for name in committed:
        assert json.loads((tmp_path / name).read_text()) == json.loads((ROOT / "n8n" / "workflows" / name).read_text()), \
            f"{name} is stale: run scripts/gen_n8n_workflows.py"
