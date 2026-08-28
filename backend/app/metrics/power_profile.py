"""Mean-maximal effort curves, critical power, and rider-type classification.

A power-duration curve is the best average an athlete has sustained for each of a
set of durations. It needs per-sample streams: activity averages cannot tell you
what someone held for five minutes. Per-activity bests are therefore computed once
at ingest and stored on the metrics row, so building a curve over years of history
is an aggregation rather than a re-read of every stream.

The same machinery answers the running question, because a mean-maximal *speed*
curve is the identical calculation on a different channel. Race prediction is then
a fit over that curve rather than a separate model.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import log

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.domain.models import Activity, ActivityMetrics

# Durations span a sprint to a long endurance effort. Anything longer is better
# served by the activity itself than by a curve point.
CURVE_DURATIONS_S = (1, 5, 10, 15, 30, 60, 120, 300, 480, 600, 1200, 1800, 2400, 3600, 5400, 7200, 10800)

# Coggan-style anchors for rider type, as a fraction of the athlete's own 5-minute
# power. Ratios are used rather than absolute W/kg so the classification does not
# require a body weight to be set.
SPRINT_ANCHOR_S = 15
VO2_ANCHOR_S = 300
THRESHOLD_ANCHOR_S = 1200


@dataclass(frozen=True)
class CurvePoint:
    duration_s: int
    value: float
    activity_id: int | None
    date: str | None


def mean_maximal(samples: list[dict], key: str, durations=CURVE_DURATIONS_S) -> dict[str, float]:
    """Best duration-weighted rolling mean for each requested window.

    Returned keyed by seconds-as-string so it round-trips through a JSON column.
    """
    from app.metrics.terrain import sample_durations
    from app.metrics.thresholds import rolling_best_average

    if not samples:
        return {}
    # Derived once and reused across every window; it parses each timestamp, so doing
    # it per window made a 17-point curve 17x more expensive than it needed to be.
    per_sample = sample_durations(samples)
    total = sum(per_sample)
    out: dict[str, float] = {}
    for seconds in durations:
        if seconds > total:
            break  # durations are ascending, so nothing longer can fit either
        best = rolling_best_average(samples, key, seconds, durations=per_sample)
        if best is not None and best > 0:
            out[str(seconds)] = round(best, 1)
    return out


def _stored_curve(metrics: ActivityMetrics, channel: str) -> dict:
    details = metrics.details if isinstance(metrics.details, dict) else {}
    curve = details.get(f"mean_max_{channel}")
    return curve if isinstance(curve, dict) else {}


def build_curve(db: Session, athlete_id: int, channel: str = "power", days: int = 365,
                sports: tuple[str, ...] = ("cycling",)) -> list[CurvePoint]:
    """Aggregate the best value at each duration across a period."""
    since = utcnow() - __import__("datetime").timedelta(days=days)
    rows = db.execute(
        select(Activity, ActivityMetrics).join(ActivityMetrics).where(
            Activity.athlete_id == athlete_id,
            Activity.start_time >= since,
            Activity.sport.in_(sports),
        )
    ).all()

    best: dict[int, tuple[float, int, str]] = {}
    for activity, metrics in rows:
        for key, value in _stored_curve(metrics, channel).items():
            try:
                seconds, watts = int(key), float(value)
            except (TypeError, ValueError):
                continue
            current = best.get(seconds)
            if current is None or watts > current[0]:
                best[seconds] = (watts, activity.id, activity.start_time.date().isoformat())

    return [CurvePoint(s, v[0], v[1], v[2]) for s, v in sorted(best.items())]


def critical_power(curve: list[CurvePoint]) -> dict | None:
    """Fit critical power and W' from the curve.

    Uses the two-parameter hyperbolic model in its linear form, P = W'/t + CP, over
    the 2-20 minute points where it holds best. Outside that band the model
    over-predicts short efforts and under-predicts long ones, so points beyond it
    are deliberately excluded rather than silently degrading the fit.
    """
    usable = [p for p in curve if 120 <= p.duration_s <= 1200 and p.value > 0]
    if len(usable) < 2:
        return None
    # Least squares of P against 1/t: slope is W', intercept is CP.
    xs = [1.0 / p.duration_s for p in usable]
    ys = [p.value for p in usable]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    if intercept <= 0 or slope <= 0:
        return None
    return {
        "critical_power_w": round(intercept, 1),
        "w_prime_j": round(slope, 0),
        "points_used": len(usable),
        "method": "two_parameter_linear_p_vs_inverse_t",
        "confidence": "medium" if len(usable) >= 4 else "low",
        "caveat": "Fitted over 2-20 minute efforts. It is only as good as how hard those efforts actually were.",
    }


def _at(curve: list[CurvePoint], seconds: int) -> float | None:
    exact = next((p.value for p in curve if p.duration_s == seconds), None)
    if exact is not None:
        return exact
    # Interpolate in log-duration, which is how a power curve behaves.
    below = [p for p in curve if p.duration_s < seconds]
    above = [p for p in curve if p.duration_s > seconds]
    if not below or not above:
        return None
    lo, hi = below[-1], above[0]
    span = log(hi.duration_s) - log(lo.duration_s)
    if span <= 0:
        return None
    ratio = (log(seconds) - log(lo.duration_s)) / span
    return lo.value + (hi.value - lo.value) * ratio


def rider_type(curve: list[CurvePoint]) -> dict | None:
    """Classify the shape of the curve, not its absolute height.

    Sprint and threshold power are expressed relative to the athlete's own
    5-minute power, so this describes what kind of rider they are without needing
    a body weight.
    """
    sprint = _at(curve, SPRINT_ANCHOR_S)
    vo2 = _at(curve, VO2_ANCHOR_S)
    threshold = _at(curve, THRESHOLD_ANCHOR_S)
    if not vo2 or vo2 <= 0:
        return None
    sprint_ratio = round(sprint / vo2, 2) if sprint else None
    threshold_ratio = round(threshold / vo2, 2) if threshold else None

    if sprint_ratio and sprint_ratio >= 2.4:
        label, reason = "Sprinter", "very high short-duration power relative to 5-minute power"
    elif threshold_ratio and threshold_ratio >= 0.92:
        label, reason = "Time triallist", "sustained power holds up unusually well toward 20 minutes"
    elif sprint_ratio and sprint_ratio <= 1.8 and threshold_ratio and threshold_ratio >= 0.85:
        label, reason = "Climber", "limited sprint with strong sustained power"
    elif sprint_ratio and threshold_ratio:
        label, reason = "All-rounder", "no single duration dominates the curve"
    else:
        return None
    return {
        "type": label, "reason": reason,
        "sprint_to_vo2_ratio": sprint_ratio, "threshold_to_vo2_ratio": threshold_ratio,
        "caveat": "Describes the shape of your recorded efforts. It is not a verdict on potential.",
    }


def curve_payload(db: Session, athlete_id: int, weight_kg: float | None, days: int = 365,
                  compare_days: int | None = None) -> dict:
    """Everything the power-profile view needs."""
    curve = build_curve(db, athlete_id, "power", days)
    comparison = build_curve(db, athlete_id, "power", compare_days) if compare_days else []
    comparison_by_duration = {p.duration_s: p.value for p in comparison}

    points = [{
        "duration_s": p.duration_s,
        "watts": p.value,
        "w_per_kg": round(p.value / weight_kg, 2) if weight_kg else None,
        "activity_id": p.activity_id,
        "date": p.date,
        "previous_watts": comparison_by_duration.get(p.duration_s),
    } for p in curve]

    return {
        "channel": "power",
        "period_days": days,
        "compare_days": compare_days,
        "weight_kg": weight_kg,
        "points": points,
        "critical_power": critical_power(curve),
        "rider_type": rider_type(curve),
        "has_data": bool(points),
    }
