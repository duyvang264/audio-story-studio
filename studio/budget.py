"""Budget Manager: per-provider cost estimates, reservations, commits.

Internal limits (this module) are independent from the real billing limits configured
at each provider. Paid providers are refused unless ALLOW_PAID_API=true, and even then
need an approved 'cost' approval when REQUIRE_APPROVAL_FOR_PAID_GENERATION=true.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Settings, get_settings
from .db import connect

# Estimated VND cost per unit. Free/local/subscription providers cost 0 VND here; their
# real constraint is quota (see CODEX_MAX_IMAGES_PER_DAY), tracked via units.
PROVIDER_PRICING = {
    ("image", "mock"): (0, False),
    ("image", "codex"): (0, False),     # ChatGPT subscription quota, not the paid API
    ("image", "local"): (0, False),
    ("tts", "macos_say"): (0, False),
    ("tts", "piper"): (0, False),
    ("tts", "tone"): (0, False),
    ("tts", "vieneu"): (0, False),
}


class BudgetError(RuntimeError):
    pass


@dataclass
class Reservation:
    id: int
    estimated_vnd: int


def period_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def category_limit(category: str, s: Settings) -> int:
    return {"image": s.image_budget_vnd, "tts": s.tts_budget_vnd}.get(category, s.reserve_budget_vnd)


def spent(category: str | None = None, period: str | None = None, conn=None) -> int:
    sql = """SELECT COALESCE(SUM(CASE WHEN status='committed' THEN COALESCE(actual_vnd, estimated_vnd)
                                      ELSE estimated_vnd END), 0) AS total
             FROM provider_costs WHERE period=%s AND status IN ('reserved','committed')"""
    params: list = [period or period_now()]
    if category:
        sql += " AND category=%s"
        params.append(category)

    def _q(c):
        return int(c.execute(sql, params).fetchone()["total"])

    if conn is not None:
        return _q(conn)
    with connect() as c:
        return _q(c)


def reserve(category: str, provider: str, units: float = 1, *, job_id: int | None = None,
            episode_id: int | None = None, purpose: str = "generate",
            settings: Settings | None = None) -> Reservation:
    """Reserve budget atomically (advisory lock serialises concurrent reservations)."""
    s = settings or get_settings()
    unit_price, is_paid = PROVIDER_PRICING.get((category, provider), (None, True))
    if unit_price is None:
        raise BudgetError(f"provider {provider!r} has no pricing entry; refusing (treated as paid/unknown)")
    if is_paid and not s.allow_paid_api:
        raise BudgetError(f"paid provider {provider!r} blocked: ALLOW_PAID_API=false")
    estimated = int(round(unit_price * units))
    with connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(7243002)")
        period = period_now()
        if estimated > 0:
            cat_total = spent(category, period, conn=c)
            if cat_total + estimated > category_limit(category, s):
                raise BudgetError(f"{category} budget exceeded: {cat_total}+{estimated} > {category_limit(category, s)} VND")
            total = spent(None, period, conn=c)
            if total + estimated > s.monthly_budget_vnd - s.reserve_budget_vnd:
                raise BudgetError(f"monthly budget exceeded: {total}+{estimated} VND")
        row = c.execute("""
            INSERT INTO provider_costs (period, category, provider, is_paid, status, estimated_vnd, units, job_id,
                                        episode_id, purpose)
            VALUES (%s,%s,%s,%s,'reserved',%s,%s,%s,%s,%s) RETURNING id
        """, (period, category, provider, is_paid, estimated, units, job_id, episode_id, purpose)).fetchone()
    return Reservation(row["id"], estimated)


def commit(res: Reservation, actual_vnd: int | None = None, note: str | None = None) -> None:
    with connect() as c:
        c.execute("""UPDATE provider_costs SET status='committed', actual_vnd=%s, note=%s, settled_at=now()
                     WHERE id=%s AND status='reserved'""",
                  (res.estimated_vnd if actual_vnd is None else actual_vnd, note, res.id))


def release(res: Reservation, note: str | None = None) -> None:
    with connect() as c:
        c.execute("""UPDATE provider_costs SET status='released', note=%s, settled_at=now()
                     WHERE id=%s AND status='reserved'""", (note, res.id))


def units_today(category: str, provider: str) -> float:
    with connect() as c:
        row = c.execute("""SELECT COALESCE(SUM(units),0) AS u FROM provider_costs
                           WHERE category=%s AND provider=%s AND status IN ('reserved','committed')
                           AND created_at >= date_trunc('day', now())""", (category, provider)).fetchone()
        return float(row["u"])


def summary(settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    period = period_now()
    with connect() as c:
        rows = c.execute("""
            SELECT category, provider, status, COUNT(*) AS n, SUM(units) AS units,
                   SUM(estimated_vnd) AS est, SUM(COALESCE(actual_vnd,0)) AS actual
            FROM provider_costs WHERE period=%s GROUP BY 1,2,3 ORDER BY 1,2,3""", (period,)).fetchall()
        total = spent(None, period, conn=c)
    return {
        "period": period,
        "monthly_budget_vnd": s.monthly_budget_vnd,
        "spent_or_reserved_vnd": total,
        "remaining_vnd": s.monthly_budget_vnd - total,
        "allow_paid_api": s.allow_paid_api,
        "lines": [{k: (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int) else v)
                   for k, v in r.items()} for r in rows],
    }
