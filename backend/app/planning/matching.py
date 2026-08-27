from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MatchScore:
    score: float
    reasons: list[str]


def activity_match_score(workout, activity) -> MatchScore:
    """Score a planned workout/activity pair from 0 to 1 with transparent reasons."""
    if workout.sport != activity.sport:
        return MatchScore(0.0, ["sport mismatch"])
    seconds = abs((activity.start_time - workout.scheduled_at).total_seconds())
    if seconds > 36 * 3600:
        return MatchScore(0.0, ["outside 36-hour matching window"])

    score = 0.45
    reasons = ["same sport"]
    if seconds <= 4 * 3600:
        score += 0.25
        reasons.append("start time within 4h")
    elif seconds <= 12 * 3600:
        score += 0.15
        reasons.append("start time within 12h")
    elif seconds <= 24 * 3600:
        score += 0.08
        reasons.append("start time within 24h")

    if workout.duration_s and activity.duration_s:
        ratio = min(workout.duration_s, activity.duration_s) / max(workout.duration_s, activity.duration_s)
        score += 0.18 * ratio
        if ratio >= 0.8:
            reasons.append("duration close")
    if workout.distance_m and activity.distance_m:
        ratio = min(workout.distance_m, activity.distance_m) / max(workout.distance_m, activity.distance_m)
        score += 0.12 * ratio
        if ratio >= 0.8:
            reasons.append("distance close")
    return MatchScore(min(round(score, 3), 1.0), reasons)
