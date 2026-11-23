"""PostgreSQL-backed job queue (FOR UPDATE SKIP LOCKED) with retry and idempotency."""
from __future__ import annotations

from typing import Any

from .db import connect, jsonb, log_event

# Pipeline stages in order. Each stage job, on success, enqueues the next one (AUTO_CHAIN).
STAGES = [
    "script_build",      # WF-02
    "story_validate",
    "scene_sound_analysis",
    "tts",               # WF-03
    "tts_qc",
    "audio_repair",
    "narration_qc",
    "sound_assets",
    "sound_timeline",
    "audio_mix",
    "audio_master",
    "master_audio_qc",
    "visual_plan",       # WF-04
    "visual_generate",   # WF-05
    "timeline",          # WF-06
    "render_preview",    # WF-07
    "qc_preview",        # WF-07
    "notify_preview",    # WF-07 -> waits for content approval
]
POST_APPROVAL_STAGES = ["render_final", "qc_final", "render_tiktok", "qc_tiktok", "publish_queue"]
ALL_JOB_TYPES = set(STAGES) | set(POST_APPROVAL_STAGES) | {"rerender_scene", "publish_upload", "drive_import", "story_generate"}

STAGE_TIMEOUTS = {
    "tts": 3600, "visual_generate": 3600, "render_preview": 7200, "render_final": 14400, "render_tiktok": 3600, "publish_upload": 7200,
}


HEARTBEAT_SEC = 60
LOCK_EXPIRY_SEC = 600  # no heartbeat for this long => the worker died


def next_stage(job_type: str) -> str | None:
    for chain in (STAGES, POST_APPROVAL_STAGES):
        if job_type in chain:
            i = chain.index(job_type)
            return chain[i + 1] if i + 1 < len(chain) else None
    return None


def enqueue(job_type: str, episode_id: int | None, *, payload: dict | None = None,
            idempotency_key: str | None = None, priority: int = 100, max_attempts: int = 3,
            conn=None) -> dict:
    """Insert a job; if the idempotency key already exists, return the existing job."""
    if job_type not in ALL_JOB_TYPES:
        raise ValueError(f"unknown job type {job_type!r}")
    key = idempotency_key or f"{job_type}:{episode_id}"
    sql = """
        INSERT INTO jobs (type, episode_id, payload, idempotency_key, priority, max_attempts, timeout_sec)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING *, true AS created
    """
    params = (job_type, episode_id, jsonb(payload or {}), key, priority, max_attempts,
              STAGE_TIMEOUTS.get(job_type, 1800))

    def _run(c):
        row = c.execute(sql, params).fetchone()
        if row is None:
            row = c.execute("SELECT *, false AS created FROM jobs WHERE idempotency_key=%s", (key,)).fetchone()
        else:
            log_event(f"job queued: {job_type}", episode_id=episode_id, job_id=row["id"], conn=c)
        return row

    if conn is not None:
        return _run(conn)
    with connect() as c:
        return _run(c)


def claim(worker_id: str) -> dict | None:
    """Atomically claim the next runnable job. Also recovers jobs whose lock expired."""
    with connect() as c:
        # Running jobs heartbeat every HEARTBEAT_SEC; a stale lock means the worker died.
        # Retry while attempts remain, otherwise fail (a job that keeps killing its worker must not loop forever).
        c.execute("""
            UPDATE jobs SET status = CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
                   locked_by=NULL, locked_at=NULL, updated_at=now(),
                   finished_at = CASE WHEN attempts < max_attempts THEN NULL ELSE now() END,
                   last_error=COALESCE(last_error,'') || ' [worker lost: lock expired]'
            WHERE status='running' AND locked_at < now() - make_interval(secs => %s)
        """, (LOCK_EXPIRY_SEC,))
        return c.execute("""
            UPDATE jobs SET status='running', attempts=attempts+1, locked_by=%s, locked_at=now(), updated_at=now()
            WHERE id = (
                SELECT id FROM jobs WHERE status='queued' AND run_after <= now()
                ORDER BY priority, id FOR UPDATE SKIP LOCKED LIMIT 1)
            RETURNING *
        """, (worker_id,)).fetchone()


def heartbeat(job_id: int) -> None:
    with connect() as c:
        c.execute("UPDATE jobs SET locked_at=now() WHERE id=%s AND status='running'", (job_id,))


def complete(job_id: int, result: Any = None) -> None:
    with connect() as c:
        c.execute("""UPDATE jobs SET status='succeeded', result=%s, locked_by=NULL, finished_at=now(),
                     updated_at=now() WHERE id=%s AND status='running'""", (jsonb(result), job_id))


def fail(job: dict, error: str, *, retryable: bool = True) -> str:
    """Record failure; requeue with exponential backoff if attempts remain. Returns new status
    ('cancelled' if the job was cancelled while it ran)."""
    can_retry = retryable and job["attempts"] < job["max_attempts"]
    status = "queued" if can_retry else "failed"
    backoff = 15 * (2 ** max(job["attempts"] - 1, 0))
    with connect() as c:
        cur = c.execute("SELECT status FROM jobs WHERE id=%s FOR UPDATE", (job["id"],)).fetchone()
        if cur and cur["status"] == "cancelled":
            return "cancelled"
        c.execute("""
            UPDATE jobs SET status=%s, last_error=%s, locked_by=NULL, locked_at=NULL,
                   run_after = now() + make_interval(secs => %s), updated_at=now(),
                   finished_at = CASE WHEN %s='failed' THEN now() ELSE NULL END
            WHERE id=%s
        """, (status, error[-4000:], backoff if can_retry else 0, status, job["id"]))
        log_event(f"job {job['type']} {'retry scheduled' if can_retry else 'FAILED'}: {error[-500:]}",
                  episode_id=job["episode_id"], job_id=job["id"], level="error", conn=c)
    return status


def cancel_episode_jobs(episode_id: int) -> int:
    with connect() as c:
        # running jobs finish their current step but are then neither completed nor chained
        rows = c.execute("""UPDATE jobs SET status='cancelled', updated_at=now(), finished_at=now()
                            WHERE episode_id=%s AND status IN ('queued','running') RETURNING id""",
                         (episode_id,)).fetchall()
        return len(rows)


def is_cancelled(job_id: int) -> bool:
    with connect() as c:
        row = c.execute("SELECT status FROM jobs WHERE id=%s", (job_id,)).fetchone()
        return row is None or row["status"] == "cancelled"
