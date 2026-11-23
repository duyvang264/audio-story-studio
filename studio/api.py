"""Worker API used by n8n. Every /api route needs the X-API-Key header. Nothing here runs shell."""
from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from . import budget, commands, jobs, pipeline, story
from .config import ensure_storage, get_settings
from .db import connect

@asynccontextmanager
async def lifespan(_app):
    ensure_storage()
    yield


app = FastAPI(title="Audio Story Studio API", version="0.1.0", lifespan=lifespan)


def require_key(x_api_key: str = Header(default="")) -> None:
    key = get_settings().api_key
    if not key or not hmac.compare_digest(x_api_key.encode(), key.encode()):
        raise HTTPException(status_code=401, detail="invalid API key")


@app.get("/health")
def health():
    with connect() as c:
        c.execute("SELECT 1")
    return {"ok": True}


@app.post("/api/telegram/update", dependencies=[Depends(require_key)])
def telegram_update(update: dict):
    """Raw Telegram update forwarded by n8n WF-00. Returns quickly; work is queued."""
    return commands.handle_update(update)


class ImportStory(BaseModel):
    path: str = Field(description="path to a .md/.txt file under storage/stories or samples/")


@app.post("/api/stories/import", dependencies=[Depends(require_key)])
def import_story(body: ImportStory):
    s = get_settings()
    p = Path(body.path).resolve()
    allowed = [(s.storage / "stories").resolve(), (commands.PROJECT_ROOT / "samples").resolve()]
    if not any(p.is_relative_to(a) for a in allowed) or p.suffix not in (".md", ".txt") or not p.exists():
        raise HTTPException(400, "path must be an existing .md/.txt under storage/stories or samples")
    res = story.import_story(p)
    return {"story_id": res["story"]["id"], "created": res["created"], "rights_status": res["story"]["rights_status"]}


class StageRequest(BaseModel):
    run: str = Field(default="manual", pattern=r"^[A-Za-z0-9_\-]{1,40}$")
    chat_id: int | None = None
    regenerate_chunks: list[int] = Field(default_factory=list, max_length=100)


@app.post("/api/episodes/{episode_id}/stages/{stage}", dependencies=[Depends(require_key)])
def enqueue_stage(episode_id: int, stage: str, body: StageRequest):
    if stage not in jobs.ALL_JOB_TYPES:
        raise HTTPException(400, f"unknown stage {stage}")
    if any(n <= 0 for n in body.regenerate_chunks):
        raise HTTPException(400, 'regenerate_chunks must contain positive generation-plan chunk numbers')
    job = jobs.enqueue(stage, episode_id, payload={"chat_id": body.chat_id, "run": body.run, "regenerate_chunks": body.regenerate_chunks},
                       idempotency_key=f"{stage}:{episode_id}:{body.run}")
    return {"job_id": job["id"], "status": job["status"], "created": job["created"]}


class StoryGenerationRequest(BaseModel):
    brief: dict
    continuity: dict = Field(default_factory=dict)
    chat_id: int | None = None
    run: str = Field(default='manual', pattern=r'^[A-Za-z0-9_\-]{1,40}$')


@app.post('/api/stories/generate', dependencies=[Depends(require_key)])
def generate_story(body: StoryGenerationRequest):
    from .audio_engine.io import stable_hash
    key=stable_hash([body.brief,body.continuity])[:20]
    job=jobs.enqueue('story_generate',None,payload=body.model_dump(),idempotency_key=f'story_generate:{key}:{body.run}',max_attempts=2)
    return {'job_id':job['id'],'status':job['status'],'created':job['created'],'provider':'local-only'}


@app.post("/api/episodes/{episode_id}/start", dependencies=[Depends(require_key)])
def start_episode(episode_id: int, body: StageRequest):
    try:
        job = pipeline.start_episode_pipeline(episode_id, chat_id=body.chat_id, run=body.run)
    except pipeline.ApprovalError as e:
        raise HTTPException(409, str(e))
    return {"job_id": job["id"], "created": job["created"]}


@app.get("/api/episodes/{episode_id}", dependencies=[Depends(require_key)])
def episode_status(episode_id: int):
    with connect() as c:
        e = c.execute("SELECT * FROM episodes WHERE id=%s", (episode_id,)).fetchone()
        if not e:
            raise HTTPException(404, "episode not found")
        js = c.execute("SELECT id, type, status, attempts, last_error, updated_at FROM jobs WHERE episode_id=%s ORDER BY id",
                       (episode_id,)).fetchall()
        sc = c.execute("SELECT scene_no, start_sec, end_sec, location_id, characters, mood, motion, status, match_type "
                       "FROM scenes WHERE episode_id=%s ORDER BY scene_no", (episode_id,)).fetchall()
    return {"episode": e, "jobs": js, "scenes": sc}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_key)])
def job_status(job_id: int):
    with connect() as c:
        j = c.execute("SELECT * FROM jobs WHERE id=%s", (job_id,)).fetchone()
    if not j:
        raise HTTPException(404, "job not found")
    return j


class Decision(BaseModel):
    approve: bool
    user_id: int
    note: str | None = None


@app.post("/api/approvals/{approval_id}/decision", dependencies=[Depends(require_key)])
def approval_decision(approval_id: int, body: Decision):
    if body.user_id not in get_settings().allowed_user_ids:
        raise HTTPException(403, "user not allowed")
    try:
        res = pipeline.decide(approval_id, body.approve, body.user_id, note=body.note)
    except pipeline.ApprovalError as e:
        raise HTTPException(404, str(e))
    return {"status": res["approval"]["status"], "changed": res["changed"]}


@app.get("/api/budget", dependencies=[Depends(require_key)])
def budget_summary():
    return budget.summary()


class Notify(BaseModel):
    chat_id: int | None = None
    text: str = Field(max_length=3500)


@app.post("/api/notify", dependencies=[Depends(require_key)])
def notify(body: Notify):
    s = get_settings()
    chat = body.chat_id or s.telegram_notify_chat_id
    if not chat:
        raise HTTPException(400, "no chat id")
    from .telegram import TelegramClient
    TelegramClient(s).send_message(chat, body.text)
    return {"ok": True}


@app.get('/api/episodes/{episode_id}/audio/qc', dependencies=[Depends(require_key)])
def audio_qc_status(episode_id: int):
    import json
    d = get_settings().storage / 'episodes' / str(episode_id) / 'audio_engine' / 'qc'
    return {'reports': {p.parent.name: json.loads(p.read_text()) for p in d.glob('*/audio_qc_report.json')}}


class AudioReview(Decision):
    stage: Literal['source','narration','premaster','master','encoded_final','encoded_tiktok'] = 'source'


@app.post('/api/episodes/{episode_id}/audio/review', dependencies=[Depends(require_key)])
def audio_listening_review(episode_id: int, body: AudioReview):
    from .audio_engine.gate import record_review, AudioQCError
    if body.user_id not in get_settings().allowed_user_ids:
        raise HTTPException(403, 'user not allowed')
    path = get_settings().storage / 'episodes' / str(episode_id) / 'audio_engine' / 'qc' / body.stage / 'audio_qc_report.json'
    if not path.exists():
        raise HTTPException(404, 'QC report not found')
    try:
        review = record_review(path, 'accepted' if body.approve else 'rejected', str(body.user_id), body.note or '')
    except (AudioQCError, ValueError) as exc:
        raise HTTPException(409, str(exc))
    return {'review': str(review), 'publication': 'still requires final encoded QC and manual publish approval'}
