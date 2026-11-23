"""Worker API: authentication, validation, no path traversal, Telegram allowlist."""
import pytest
from fastapi.testclient import TestClient

from studio.api import app

pytestmark = pytest.mark.db
KEY = {"X-API-Key": "test-key"}


@pytest.fixture(scope="module")
def client(db):
    with TestClient(app) as c:
        yield c


def test_health_is_public(client):
    assert client.get("/health").json() == {"ok": True}


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_api_requires_key(client, headers):
    assert client.get("/api/budget", headers=headers).status_code == 401


def test_budget(client):
    body = client.get("/api/budget", headers=KEY).json()
    assert body["monthly_budget_vnd"] == 500000 and body["allow_paid_api"] is False


def test_unknown_stage_rejected(client):
    assert client.post("/api/episodes/1/stages/rm", json={"run": "x"}, headers=KEY).status_code == 400


def test_run_tag_validated(client):
    r = client.post("/api/episodes/1/stages/tts", json={"run": "x; rm -rf /"}, headers=KEY)
    assert r.status_code == 422


@pytest.mark.parametrize("path", ["/etc/passwd", "../../etc/hosts.md", "samples/../../secret.md"])
def test_story_import_path_confined(client, path):
    assert client.post("/api/stories/import", json={"path": path}, headers=KEY).status_code == 400


def test_telegram_update_unauthorised_user(client):
    upd = {"update_id": 77001, "message": {"from": {"id": 42}, "chat": {"id": 42}, "text": "/budget"}}
    assert client.post("/api/telegram/update", json=upd, headers=KEY).json()["reason"] == "unauthorized"


def test_approval_decision_requires_allowed_user(client):
    r = client.post("/api/approvals/1/decision", json={"approve": True, "user_id": 42}, headers=KEY)
    assert r.status_code == 403


def test_audio_qc_endpoint_keeps_auth_boundary():
    from fastapi.testclient import TestClient
    from studio.api import app
    with TestClient(app) as client:
        assert client.get('/api/episodes/9999/audio/qc').status_code == 401
        response=client.get('/api/episodes/9999/audio/qc',headers={'X-API-Key':'test-key'})
        assert response.status_code==200 and response.json()=={'reports':{}}
        response=client.post('/api/episodes/9999/audio/review',headers={'X-API-Key':'test-key'},json={'approve':True,'user_id':666,'note':'not authorized','stage':'source'})
        assert response.status_code==403
