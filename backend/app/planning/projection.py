from __future__ import annotations
from datetime import date, datetime, timedelta
from typing import Iterable

from app.core.config import settings


def ewma_step(previous: float, load: float, days: float) -> float:
    return previous + (load - previous) / days


def project_load_series(
    actual_daily: dict[date, float],
    planned_daily: dict[date, float],
    start: date,
    end: date,
    fitness_days: float | None = None,
    fatigue_days: float | None = None,
    today: date | None = None,
) -> list[dict]:
    """Build an actual + projected fitness curve.

    Actual load wins through today. Planned load is used only for future dates, so
    a planned session never double-counts a completed day in the projection.
    """
    today = today or date.today()
    fitness_days = float(fitness_days if fitness_days is not None else settings.fitness_tau_days)
    fatigue_days = float(fatigue_days if fatigue_days is not None else settings.fatigue_tau_days)
    fitness = 0.0
    fatigue = 0.0
    rows: list[dict] = []
    d = start
    while d <= end:
        is_projected = d > today
        actual = float(actual_daily.get(d, 0.0))
        planned = float(planned_daily.get(d, 0.0))
        load = planned if is_projected else actual
        fitness = ewma_step(fitness, load, fitness_days)
        fatigue = ewma_step(fatigue, load, fatigue_days)
        rows.append({
            "date": d.isoformat(),
            "load": round(load, 1),
            "actual_load": round(actual, 1),
            "planned_load": round(planned, 1),
            "fitness": round(fitness, 1),
            "fatigue": round(fatigue, 1),
            "form": round(fitness - fatigue, 1),
            "projected": is_projected,
        })
        d += timedelta(days=1)
    return rows


def weekly_totals(rows: Iterable[dict]) -> list[dict]:
    buckets: dict[date, dict] = {}
    for row in rows:
        d = date.fromisoformat(row["date"])
        monday = d - timedelta(days=d.weekday())
        bucket = buckets.setdefault(monday, {"week": monday.isoformat(), "load": 0.0, "planned_load": 0.0, "actual_load": 0.0})
        bucket["load"] += float(row.get("load", 0))
        bucket["planned_load"] += float(row.get("planned_load", 0))
        bucket["actual_load"] += float(row.get("actual_load", 0))
    return [
        {k: (round(v, 1) if isinstance(v, float) else v) for k, v in bucket.items()}
        for _, bucket in sorted(buckets.items())
    ]


def projection_warnings(rows: list[dict], key_sessions: Iterable[tuple[datetime, str, float]] = ()) -> list[dict]:
    """Return explainable planning warnings, never opaque plan rejection."""
    warnings: list[dict] = []
    weeks = weekly_totals(rows)
    previous = None
    for week in weeks:
        load = float(week["load"])
        if previous and previous >= 100 and load > previous * 1.15:
            warnings.append({
                "code": "weekly_load_spike",
                "severity": "warning" if load <= previous * 1.30 else "high",
                "week": week["week"],
                "message": f"Projected weekly load rises {round((load / previous - 1) * 100)}% from {round(previous)} to {round(load)}.",
            })
        if load > 900:
            warnings.append({"code": "very_high_weekly_load", "severity": "high", "week": week["week"], "message": f"Projected load of {round(load)} is unusually high; review duration and intensity."})
        previous = load if load > 0 else previous

    sessions = sorted(key_sessions, key=lambda item: item[0])
    # Must match the labels infer_intensity can actually return.
    hard = {"threshold", "vo2", "anaerobic", "race"}
    for left, right in zip(sessions, sessions[1:]):
        if left[1] in hard and right[1] in hard and (right[0] - left[0]).total_seconds() < 24 * 3600:
            warnings.append({
                "code": "stacked_key_sessions",
                "severity": "warning",
                "date": right[0].date().isoformat(),
                "message": f"Key sessions are stacked within 24 hours ({left[1]} then {right[1]}).",
            })
    return warnings
