"""Running race-time prediction from recorded efforts.

Two independent methods, reported side by side rather than blended into one
number that hides its own uncertainty:

1. **Riegel** extrapolates from a single best effort using t2 = t1 * (d2/d1)^k.
   It is the standard, and it is honest about being an extrapolation.
2. **Critical speed** fits the two-parameter model to the mean-maximal speed
   curve, then solves for the target distance. It uses more of the history but
   needs sustained efforts in the 2-20 minute band.

Confidence is driven by how far the prediction reaches beyond the evidence.
Predicting a marathon from a 5 km best is arithmetic, not a forecast, and is
labelled accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.domain.models import Activity
from app.metrics.load import resolve_durations

RACE_DISTANCES = (
    ("5K", 5000.0),
    ("10K", 10000.0),
    ("Half marathon", 21097.5),
    ("Marathon", 42195.0),
)

# Riegel's original exponent is 1.06. It is known to under-predict the marathon for
# athletes without long-run history, which the confidence rules below account for
# rather than by silently inflating the exponent.
RIEGEL_EXPONENT = 1.06

# Beyond this multiple of the evidence distance, a prediction is extrapolation.
RELIABLE_EXTRAPOLATION = 2.0
MAX_EXTRAPOLATION = 4.0


@dataclass(frozen=True)
class BestEffort:
    distance_m: float
    duration_s: float
    activity_id: int
    date: str
    sport: str


def _pace(seconds: float, metres: float) -> str:
    if metres <= 0 or seconds <= 0:
        return "-"
    per_km = seconds / (metres / 1000.0)
    return f"{int(per_km // 60)}:{int(round(per_km % 60)):02d}/km"


def _hms(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def best_efforts(db: Session, athlete_id: int, days: int = 365) -> list[BestEffort]:
    """Fastest recorded run at or above each race distance.

    Road and treadmill running only. Trail and mountain running are excluded
    because terrain makes raw pace meaningless as race evidence.
    """
    since = utcnow() - timedelta(days=days)
    rows = list(db.scalars(select(Activity).where(
        Activity.athlete_id == athlete_id,
        Activity.sport == "running",
        Activity.start_time >= since,
        Activity.distance_m.isnot(None),
    )))
    efforts: list[BestEffort] = []
    for _, distance in RACE_DISTANCES:
        candidates = []
        for activity in rows:
            if (activity.distance_m or 0) < distance * 0.97:
                continue
            # Moving time, and scaled down when the run was longer than the target,
            # so a 12 km run is usable evidence for a 10 km.
            moving = resolve_durations(activity.duration_s, activity.moving_time_s).metabolic_s
            if moving <= 0:
                continue
            scaled = moving * (distance / activity.distance_m)
            candidates.append((scaled, activity))
        if candidates:
            duration, activity = min(candidates, key=lambda c: c[0])
            efforts.append(BestEffort(distance, duration, activity.id,
                                      activity.start_time.date().isoformat(), activity.sport))
    return efforts


def riegel(evidence: BestEffort, target_m: float) -> float:
    return evidence.duration_s * (target_m / evidence.distance_m) ** RIEGEL_EXPONENT


def _confidence(evidence_m: float, target_m: float, evidence_date: str) -> tuple[str, str | None]:
    ratio = target_m / evidence_m
    if ratio > MAX_EXTRAPOLATION:
        return "low", f"Extrapolated {ratio:.1f}x beyond your longest comparable effort. Treat as indicative only."
    if ratio > RELIABLE_EXTRAPOLATION:
        return "low", f"Extrapolated {ratio:.1f}x beyond your evidence. Endurance at this distance is unproven."
    if ratio > 1.15:
        return "medium", None
    return "high", None


def predict(db: Session, athlete_id: int, days: int = 365,
            critical_speed_mps: float | None = None) -> dict:
    efforts = best_efforts(db, athlete_id, days)
    if not efforts:
        return {"predictions": [], "evidence": [], "has_data": False,
                "note": "No road-running activities with distance were found in the selected period."}

    predictions = []
    for label, target in RACE_DISTANCES:
        # Predict from the closest evidence distance, which minimises extrapolation.
        source = min(efforts, key=lambda e: abs(e.distance_m - target))
        seconds = riegel(source, target)
        confidence, caveat = _confidence(source.distance_m, target, source.date)

        cs_seconds = None
        if critical_speed_mps and critical_speed_mps > 0:
            # Critical speed is a sustainable-pace ceiling, so a longer race is run
            # slightly below it and a shorter one above.
            factor = 1.06 if target <= 5000 else 1.0 if target <= 21098 else 0.94
            cs_seconds = target / (critical_speed_mps * factor)

        predictions.append({
            "distance": label,
            "distance_m": target,
            "riegel_seconds": round(seconds),
            "riegel_time": _hms(seconds),
            "riegel_pace": _pace(seconds, target),
            "critical_speed_seconds": round(cs_seconds) if cs_seconds else None,
            "critical_speed_time": _hms(cs_seconds) if cs_seconds else None,
            "confidence": confidence,
            "caveat": caveat,
            "based_on": {
                "distance_m": source.distance_m, "date": source.date,
                "time": _hms(source.duration_s), "pace": _pace(source.duration_s, source.distance_m),
                "activity_id": source.activity_id,
            },
        })

    return {
        "predictions": predictions,
        "evidence": [{
            "distance_m": e.distance_m, "time": _hms(e.duration_s),
            "pace": _pace(e.duration_s, e.distance_m), "date": e.date, "activity_id": e.activity_id,
        } for e in efforts],
        "has_data": True,
        "method_note": (
            "Riegel extrapolates from your closest recorded effort. Critical speed, when shown, "
            "comes from your threshold pace. Both assume road conditions and a genuine race effort; "
            "neither knows about heat, terrain or fuelling."
        ),
    }
