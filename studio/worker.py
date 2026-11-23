"""Job worker: claims jobs from PostgreSQL and runs pipeline stages."""
from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
import traceback

from . import jobs, pipeline
from .config import ensure_storage, get_settings

log = logging.getLogger("studio.worker")
_stop = False


def _handle_stop(*_):
    global _stop
    _stop = True


def run_one(s=None) -> bool:
    """Claim and run a single job. Returns False when the queue is empty."""
    s = s or get_settings()
    job = jobs.claim(s.worker_id)
    if job is None:
        return False
    log.info("job %s %s episode=%s attempt=%s", job["id"], job["type"], job["episode_id"], job["attempts"])
    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(job["id"], stop), daemon=True)
    beat.start()
    try:
        result = pipeline.run_job(job, s)
    except Exception as e:  # noqa: BLE001 — every failure is recorded on the job
        err = f"{type(e).__name__}: {e}"
        log.error("job %s failed: %s\n%s", job["id"], err, traceback.format_exc())
        status = jobs.fail(job, err, retryable=getattr(e, "retryable", True))
        if status == "cancelled":
            _after_cancel(job)
        else:
            _safe(pipeline.after_failure, job, err, status == "failed", s)
        return True
    finally:
        stop.set()
        beat.join(timeout=5)
    if jobs.is_cancelled(job["id"]):
        _after_cancel(job)
        return True
    jobs.complete(job["id"], result)
    _safe(pipeline.after_success, job, s)
    return True


def _heartbeat(job_id: int, stop: threading.Event) -> None:
    """Keep the lock fresh while the job runs, so only dead workers' jobs are ever recovered."""
    while not stop.wait(jobs.HEARTBEAT_SEC):
        try:
            jobs.heartbeat(job_id)
        except Exception:  # noqa: BLE001 — a missed beat is retried on the next tick
            log.warning("heartbeat for job %s failed", job_id)


def _after_cancel(job: dict) -> None:
    if job["episode_id"]:
        pipeline.set_stage(job["episode_id"], "cancelled", "cancelled")
    log.info("job %s was cancelled while running; not chaining", job["id"])


def _safe(fn, *args) -> None:
    """Notifications/chaining errors (e.g. Telegram down) must never crash the worker."""
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        log.exception("%s failed", fn.__name__)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audio Story Studio worker")
    ap.add_argument("--until-idle", action="store_true", help="exit when no runnable job remains")
    ap.add_argument("--poll", type=float, default=2.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Telegram API URLs contain the bot token; do not log HTTP request URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = get_settings()
    ensure_storage(s)
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    while not _stop:
        if not run_one(s):
            if args.until_idle:
                break
            time.sleep(args.poll)


if __name__ == "__main__":
    main()
