"""PostgreSQL access helpers and migration runner."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import PROJECT_ROOT, get_settings

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


@contextmanager
def connect(url: str | None = None) -> Iterator[psycopg.Connection]:
    """Connection whose transaction commits on clean exit and rolls back on error."""
    conn = psycopg.connect(url or get_settings().database_url, row_factory=dict_row)
    try:
        with conn.transaction():
            yield conn
    finally:
        conn.close()


def fetchone(sql: str, params: Any = None) -> dict | None:
    with connect() as c:
        return c.execute(sql, params).fetchone()


def fetchall(sql: str, params: Any = None) -> list[dict]:
    with connect() as c:
        return c.execute(sql, params).fetchall()


def execute(sql: str, params: Any = None) -> None:
    with connect() as c:
        c.execute(sql, params)


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value)


def migrate(url: str | None = None) -> list[str]:
    applied = []
    with connect(url) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        c.execute("SELECT pg_advisory_xact_lock(7243001)")
        done = {r["name"] for r in c.execute("SELECT name FROM schema_migrations").fetchall()}
        for path in sorted(Path(MIGRATIONS_DIR).glob("*.sql")):
            if path.name in done:
                continue
            c.execute(path.read_text(encoding="utf-8"))
            c.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


def log_event(message: str, *, episode_id: int | None = None, job_id: int | None = None,
              level: str = "info", data: Any = None, conn: psycopg.Connection | None = None) -> None:
    sql = "INSERT INTO job_events (job_id, episode_id, level, message, data) VALUES (%s,%s,%s,%s,%s)"
    params = (job_id, episode_id, level, message, Jsonb(data) if data is not None else None)
    if conn is not None:
        conn.execute(sql, params)
    else:
        execute(sql, params)
