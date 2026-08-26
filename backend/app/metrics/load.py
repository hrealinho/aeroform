from dataclasses import dataclass
from math import exp


@dataclass(frozen=True)
class LoadResult:
    load: float
    method: str
    confidence: str
    details: dict
    intensity_factor: float | None = None
    trimp: float | None = None
    mechanical_load: float | None = None


def power_load(duration_s: float, normalized_power: float, threshold_power: float) -> LoadResult:
    if duration_s <= 0 or normalized_power <= 0 or threshold_power <= 0:
        raise ValueError("Power load requires positive duration, power and threshold")
    intensity = normalized_power / threshold_power
    load = (duration_s / 3600.0) * (intensity**2) * 100.0
    return LoadResult(round(load, 2), "power", "high", {"threshold_power": threshold_power, "normalized_power": normalized_power}, round(intensity, 4))


def hr_trimp_load(duration_s: float, avg_hr: float, resting_hr: float, max_hr: float) -> LoadResult:
    if not (max_hr > resting_hr and avg_hr > resting_hr):
        raise ValueError("Invalid heart-rate inputs")
    hrr = max(0.0, min(1.2, (avg_hr - resting_hr) / (max_hr - resting_hr)))
    minutes = duration_s / 60.0
    trimp = minutes * hrr * 0.64 * exp(1.92 * hrr)
    load = trimp * 0.85
    return LoadResult(round(load, 2), "hr_trimp", "medium", {"avg_hr": avg_hr, "resting_hr": resting_hr, "max_hr": max_hr, "hrr": round(hrr, 4)}, trimp=round(trimp, 2))


def rpe_load(duration_s: float, rpe: float) -> LoadResult:
    if duration_s <= 0 or not (0 < rpe <= 10):
        raise ValueError("RPE load requires duration and RPE 1-10")
    raw = duration_s / 60.0 * rpe
    load = raw / 5.0
    return LoadResult(round(load, 2), "session_rpe", "low", {"raw_session_rpe": round(raw, 2), "normalization": 5.0})


def mountain_mechanical_load(duration_s: float, elevation_gain_m: float | None, elevation_loss_m: float | None) -> float:
    hours = max(duration_s, 0) / 3600.0
    gain = max(elevation_gain_m or 0, 0)
    loss = max(elevation_loss_m or 0, 0)
    # Deliberately versionable heuristic. Descent is weighted higher for eccentric stress.
    return round(hours * 10 + gain / 100.0 * 2.0 + loss / 100.0 * 3.0, 2)
