"""Near-duplicate activity detection.

The fingerprint deliberately buckets distance to 20 m, which is tighter than GPS
variance on a repeated recording of the same route. Two uploads of one session -
common when a Strava account has been merged, or a watch and a head unit both
recorded - differ by 1-4% in distance and land in different buckets, so both are
stored and their load is counted twice.

These are NOT merged automatically. Each has its own Strava id, so from the
provider's perspective they are genuinely distinct activities, and silently
discarding one would destroy provenance. Candidates are surfaced for the athlete
to resolve.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import to_utc
from app.domain.models import Activity, ActivityMetrics

# A repeat recording of one session starts within a couple of minutes and matches
# closely on duration and distance. These are deliberately tight: the goal is high
# precision, because a false positive invites the athlete to delete real training.
START_TOLERANCE_S = 300.0
DURATION_RATIO = 0.90
DISTANCE_RATIO = 0.90


@dataclass(frozen=True)
class DuplicateCandidate:
    score: float
    reasons: list[str]


def _ratio(a: float | None, b: float | None) -> float | None:
    if not a or not b or a <= 0 or b <= 0:
        return None
    return min(a, b) / max(a, b)


def compare(left: Activity, right: Activity) -> DuplicateCandidate | None:
    """Score how likely two activities are the same session. None if clearly not."""
    if left.sport != right.sport:
        return None
    gap = abs((to_utc(left.start_time) - to_utc(right.start_time)).total_seconds())
    if gap > START_TOLERANCE_S:
        return None

    reasons = [f"start within {int(gap)}s", f"both {left.sport}"]
    score = 0.5

    duration_ratio = _ratio(left.duration_s, right.duration_s)
    if duration_ratio is None or duration_ratio < DURATION_RATIO:
        return None
    score += 0.25 * duration_ratio
    reasons.append(f"duration within {(1 - duration_ratio) * 100:.1f}%")

    distance_ratio = _ratio(left.distance_m, right.distance_m)
    if distance_ratio is not None:
        if distance_ratio < DISTANCE_RATIO:
            return None
        score += 0.25 * distance_ratio
        reasons.append(f"distance within {(1 - distance_ratio) * 100:.1f}%")
    elif left.distance_m or right.distance_m:
        # One has distance and the other does not: same session is less certain.
        score -= 0.1
        reasons.append("distance present on only one")

    return DuplicateCandidate(min(round(score, 3), 1.0), reasons)


def find_duplicate_groups(db: Session, athlete_id: int, limit: int = 200) -> list[dict]:
    """Group probable repeat recordings, newest first, with the load each group double-counts."""
    rows = db.execute(
        select(Activity, ActivityMetrics)
        .outerjoin(ActivityMetrics)
        .where(Activity.athlete_id == athlete_id)
        .order_by(Activity.start_time)
    ).all()
    load_by_id = {a.id: float(m.training_load or 0) if m else 0.0 for a, m in rows}
    activities = [a for a, _ in rows]

    groups: list[dict] = []
    consumed: set[int] = set()
    for index, left in enumerate(activities):
        if left.id in consumed:
            continue
        members = [left]
        match_reasons: list[str] = []
        score = 0.0
        # The list is time-ordered, so a repeat recording is always nearby.
        for right in activities[index + 1:]:
            if (to_utc(right.start_time) - to_utc(left.start_time)).total_seconds() > START_TOLERANCE_S:
                break
            if right.id in consumed:
                continue
            candidate = compare(left, right)
            if candidate:
                members.append(right)
                match_reasons = candidate.reasons
                score = max(score, candidate.score)
        if len(members) < 2:
            continue
        consumed.update(m.id for m in members)
        loads = [load_by_id.get(m.id, 0.0) for m in members]
        groups.append({
            "score": score,
            "reasons": match_reasons,
            # Everything beyond the first copy is load counted more than once.
            "duplicated_load": round(sum(sorted(loads, reverse=True)[1:]), 1),
            "activities": [{
                "id": m.id,
                "start_time": to_utc(m.start_time).isoformat(),
                "sport": m.sport,
                "name": m.name,
                "duration_s": m.duration_s,
                "distance_m": m.distance_m,
                "elevation_gain_m": m.elevation_gain_m,
                "training_load": round(load_by_id.get(m.id, 0.0), 1),
                "sources": sorted({s.source_type for s in m.sources}),
                "external_ids": sorted({s.external_id for s in m.sources if s.external_id}),
            } for m in members],
        })

    groups.sort(key=lambda g: g["activities"][0]["start_time"], reverse=True)
    return groups[:limit]


def delete_activity(db: Session, athlete_id: int, activity_id: int) -> bool:
    """Delete one activity and everything hanging off it.

    Sources, streams and metrics cascade. Planned workouts matched to it are detached by
    the foreign key rather than deleted, so the plan survives.
    """
    activity = db.get(Activity, activity_id)
    if not activity or activity.athlete_id != athlete_id:
        return False
    db.delete(activity)
    db.commit()
    return True
