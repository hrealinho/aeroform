"""Turning an intensity label into something an athlete can actually execute.

A prescription needs two things the planner did not previously carry:

1. **A unit.** Runners think in kilometres, cyclists in hours. The same session is
   "5 x 1km" or "5 x 4min" depending on who is reading it, so the athlete picks the
   unit and the planner expresses every step in it.
2. **A target.** "threshold" is not executable. A pace range or a power range is.
   Ranges rather than single numbers, because a target that reads as one exact
   number invites chasing it on a day when it is not appropriate.

Targets are derived from the athlete's stored thresholds, so they are as good as
those thresholds and no better - which is why each one carries the source it came
from and disappears entirely when the threshold is missing.
"""
from __future__ import annotations

from dataclasses import dataclass

# Fractions of threshold speed for running, and of threshold power for cycling.
# Speed and pace are inverse: an easy run is a LOWER fraction of threshold speed.
# Bands are deliberately wide - a range the athlete can sit inside, not a number to hit.
SPEED_FRACTIONS = {
    "recovery": (0.62, 0.72),
    "easy": (0.72, 0.80),
    "endurance": (0.80, 0.86),
    "steady": (0.86, 0.91),
    "tempo": (0.91, 0.96),
    "threshold": (0.96, 1.02),
    "vo2": (1.05, 1.12),
    "anaerobic": (1.12, 1.28),
    "race": (0.88, 0.94),
}

POWER_FRACTIONS = {
    "recovery": (0.40, 0.55),
    "easy": (0.55, 0.70),
    "endurance": (0.65, 0.80),
    "steady": (0.80, 0.88),
    "tempo": (0.88, 0.95),
    "threshold": (0.95, 1.05),
    "vo2": (1.05, 1.20),
    "anaerobic": (1.20, 1.50),
    "race": (0.85, 0.95),
}

RUNNING_SPORTS = {"running", "trail_running"}
PACE_SPORTS = RUNNING_SPORTS | {"hiking", "mountaineering"}

# Used only to convert between time and distance when no threshold is available, so a
# distance prescription is still possible for a new athlete. Deliberately conservative.
FALLBACK_SPEED_MPS = {
    "running": 2.9, "trail_running": 2.2, "hiking": 1.2,
    "mountaineering": 0.9, "cycling": 6.5, "climbing": 0.5,
}


@dataclass(frozen=True)
class Prescription:
    """How a single step should be executed."""

    unit: str                      # "time" or "distance"
    duration_s: float | None
    distance_m: float | None
    target: dict | None

    def as_step_fields(self) -> dict:
        fields: dict = {}
        if self.duration_s is not None:
            fields["duration_s"] = round(self.duration_s, 1)
        if self.distance_m is not None:
            fields["distance_m"] = round(self.distance_m)
        if self.target:
            fields["target"] = self.target
        return fields


def pace_string(speed_mps: float | None) -> str | None:
    """Speed in m/s as min:sec per km."""
    if not speed_mps or speed_mps <= 0:
        return None
    # Round to whole seconds BEFORE splitting, or 299.7s formats as "4:60" instead of 5:00.
    total = int(round(1000.0 / speed_mps))
    return f"{total // 60}:{total % 60:02d}"


def hms(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "0:00"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def target_for(intensity: str, sport: str, thresholds: dict) -> dict | None:
    """Build a pace or power target band for one intensity.

    ``thresholds`` is a plain mapping so this stays testable and free of database
    concerns: {"threshold_speed_mps": 4.3, "ftp": 250, "critical_power": 300}.
    Returns None when the athlete has no threshold to base a target on - an absent
    target is honest, an invented one is not.
    """
    label = (intensity or "endurance").lower()

    if sport == "cycling":
        power = thresholds.get("ftp")
        band = POWER_FRACTIONS.get(label)
        if not power or not band:
            return None
        return {
            "kind": "power",
            "min_watts": round(power * band[0]),
            "max_watts": round(power * band[1]),
            "display": f"{round(power * band[0])}-{round(power * band[1])} W",
            "source": "ftp",
        }

    if sport in PACE_SPORTS:
        speed = thresholds.get("threshold_speed_mps")
        band = SPEED_FRACTIONS.get(label)
        if not speed or not band:
            return None
        slow, fast = speed * band[0], speed * band[1]
        return {
            "kind": "pace",
            "min_speed_mps": round(slow, 3),
            "max_speed_mps": round(fast, 3),
            # Pace is inverse to speed, so the faster end is the smaller number.
            "display": f"{pace_string(fast)}-{pace_string(slow)}/km",
            "source": "threshold_speed_mps",
        }
    return None


def _reference_speed(intensity: str, sport: str, thresholds: dict) -> float:
    """Expected speed for an intensity, used to convert between time and distance."""
    band = SPEED_FRACTIONS.get((intensity or "endurance").lower())
    threshold_speed = thresholds.get("threshold_speed_mps")
    if threshold_speed and band:
        return threshold_speed * (band[0] + band[1]) / 2
    return FALLBACK_SPEED_MPS.get(sport, 2.5)


def prescribe(intensity: str, sport: str, thresholds: dict, unit: str = "time",
              duration_s: float | None = None, distance_m: float | None = None) -> Prescription:
    """Express one step in the athlete's preferred unit, with a target band.

    Both the time and the distance are always populated - they are the same session
    described two ways - so a device export or a UI can use whichever it needs. ``unit``
    records which one was authoritative, so rounding never drifts the intended one.
    """
    target = target_for(intensity, sport, thresholds)
    speed = _reference_speed(intensity, sport, thresholds)

    if unit == "distance":
        if distance_m is None:
            distance_m = (duration_s or 0) * speed
        # Round running distances to something a person would actually say.
        step = 100 if distance_m < 3000 else 500
        distance_m = max(step, round(distance_m / step) * step)
        duration_s = distance_m / speed if speed > 0 else duration_s
    else:
        if duration_s is None:
            duration_s = (distance_m or 0) / speed if speed > 0 else 0
        distance_m = duration_s * speed if speed > 0 else distance_m

    return Prescription(unit=unit, duration_s=duration_s, distance_m=distance_m, target=target)


def prescription_unit(profile: dict | None, sport: str) -> str:
    """The unit to prescribe in.

    An explicit athlete preference wins. Otherwise runners get distance and everyone
    else gets time, which is how each group already thinks about a session.
    """
    preferences = (profile or {}).get("preferences") or {}
    chosen = str(preferences.get("prescription") or "auto").lower()
    if chosen in {"time", "distance"}:
        return chosen
    return "distance" if sport in RUNNING_SPORTS else "time"
