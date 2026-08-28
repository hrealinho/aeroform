from __future__ import annotations

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


@dataclass(frozen=True)
class TerrainLoadProfile:
    """Sport-specific non-metabolic load dimensions.

    These values intentionally remain separate from the physiological load estimate.
    The composite score only blends them with explicit, versioned weights so ascent,
    descent and time-on-feet can affect trail/hiking load without pretending they are
    directly equivalent to heart-rate or power stress.
    """

    distance_load: float
    ascent_load: float
    descent_load: float
    durability_load: float
    mechanical_load: float
    vertical_load: float
    gain_per_km: float | None
    loss_per_km: float | None

    def as_dict(self) -> dict:
        return {
            "distance_load": self.distance_load,
            "ascent_load": self.ascent_load,
            "descent_load": self.descent_load,
            "durability_load": self.durability_load,
            "mechanical_load": self.mechanical_load,
            "vertical_load": self.vertical_load,
            "gain_per_km": self.gain_per_km,
            "loss_per_km": self.loss_per_km,
        }


def power_load(duration_s: float, normalized_power: float, threshold_power: float) -> LoadResult:
    if duration_s <= 0 or normalized_power <= 0 or threshold_power <= 0:
        raise ValueError("Power load requires positive duration, power and threshold")
    intensity = normalized_power / threshold_power
    load = (duration_s / 3600.0) * (intensity**2) * 100.0
    return LoadResult(
        round(load, 2),
        "power",
        "high",
        {"threshold_power": threshold_power, "normalized_power": normalized_power},
        round(intensity, 4),
    )


def hr_trimp_load(duration_s: float, avg_hr: float, resting_hr: float, max_hr: float) -> LoadResult:
    if not (max_hr > resting_hr and avg_hr > resting_hr):
        raise ValueError("Invalid heart-rate inputs")
    hrr = max(0.0, min(1.2, (avg_hr - resting_hr) / (max_hr - resting_hr)))
    minutes = duration_s / 60.0
    trimp = minutes * hrr * 0.64 * exp(1.92 * hrr)
    load = trimp * 0.85
    return LoadResult(
        round(load, 2),
        "hr_trimp",
        "medium",
        {"avg_hr": avg_hr, "resting_hr": resting_hr, "max_hr": max_hr, "hrr": round(hrr, 4)},
        trimp=round(trimp, 2),
    )


def rpe_load(duration_s: float, rpe: float) -> LoadResult:
    if duration_s <= 0 or not (0 < rpe <= 10):
        raise ValueError("RPE load requires duration and RPE 1-10")
    raw = duration_s / 60.0 * rpe
    load = raw / 5.0
    return LoadResult(
        round(load, 2),
        "session_rpe",
        "low",
        {"raw_session_rpe": round(raw, 2), "normalization": 5.0},
    )


# Coefficients are deliberately explicit and versioned as part of metric v0.5.
# The scale is tuned so road running remains close to familiar metabolic-load
# numbers while mountainous trail/hiking activities gain meaningful extra load.
# ``hour`` is the base time-on-feet rate; ``long_hour`` is the extra rate applied to
# every hour beyond ``long_after_h``. Hiking and mountaineering carry the highest
# combined durability rates, matching the documented intent that these sports are
# driven more by time on feet than by metabolic intensity.
_TERRAIN_COEFFICIENTS = {
    "running": {"distance": 3.0, "ascent": 0.8, "descent": 1.0, "hour": 2.0, "long_after_h": 1.5, "long_hour": 4.0},
    "trail_running": {"distance": 2.5, "ascent": 2.0, "descent": 3.0, "hour": 5.0, "long_after_h": 2.0, "long_hour": 8.0},
    "hiking": {"distance": 1.5, "ascent": 2.0, "descent": 2.5, "hour": 8.0, "long_after_h": 3.0, "long_hour": 9.0},
    "mountaineering": {"distance": 1.4, "ascent": 2.5, "descent": 3.0, "hour": 9.0, "long_after_h": 3.0, "long_hour": 10.0},
}


def terrain_load_profile(
    sport: str,
    duration_s: float,
    distance_m: float | None,
    elevation_gain_m: float | None,
    elevation_loss_m: float | None,
) -> TerrainLoadProfile | None:
    """Calculate distance, ascent, descent and time-on-feet load dimensions.

    Descent is deliberately weighted more strongly for trail/mountain sports to
    represent eccentric stress that power/HR frequently under-represent. The
    function does not itself claim a physiological load; it supplies transparent
    dimensions that can be blended by ``composite_training_load``.
    """

    coeff = _TERRAIN_COEFFICIENTS.get(sport)
    if coeff is None:
        return None
    hours = max(float(duration_s or 0), 0.0) / 3600.0
    km = max(float(distance_m or 0), 0.0) / 1000.0
    gain = max(float(elevation_gain_m or 0), 0.0)
    loss = max(float(elevation_loss_m or 0), 0.0)

    distance_load = km * coeff["distance"]
    ascent_load = gain / 100.0 * coeff["ascent"]
    descent_load = loss / 100.0 * coeff["descent"]
    durability_load = hours * coeff["hour"] + max(0.0, hours - coeff["long_after_h"]) * coeff["long_hour"]
    mechanical = distance_load + ascent_load + descent_load + durability_load

    return TerrainLoadProfile(
        distance_load=round(distance_load, 2),
        ascent_load=round(ascent_load, 2),
        descent_load=round(descent_load, 2),
        durability_load=round(durability_load, 2),
        mechanical_load=round(mechanical, 2),
        vertical_load=round(ascent_load + descent_load, 2),
        gain_per_km=round(gain / km, 1) if km > 0 else None,
        loss_per_km=round(loss / km, 1) if km > 0 else None,
    )


def composite_training_load(sport: str, metabolic_load: float, terrain: TerrainLoadProfile | None) -> tuple[float, dict]:
    """Blend metabolic and terrain/mechanical load on a sport-specific scale.

    This is intentionally *not* ``metabolic + elevation bonus``. Uphill work is
    already partially represented by HR/power, so the terrain component is only
    partially weighted. Hiking/mountaineering down-weight long-duration HR TRIMP
    while giving more weight to time-on-feet and vertical stress.
    """

    metabolic = max(float(metabolic_load or 0), 0.0)
    if terrain is None or sport == "cycling":
        return round(metabolic, 2), {"metabolic_weight": 1.0, "mechanical_weight": 0.0}

    weights = {
        "running": (0.90, 0.18),
        "trail_running": (0.80, 0.40),
        "hiking": (0.60, 0.65),
        "mountaineering": (0.55, 0.75),
    }
    metabolic_weight, mechanical_weight = weights.get(sport, (1.0, 0.0))
    overall = metabolic * metabolic_weight + terrain.mechanical_load * mechanical_weight
    return round(overall, 2), {
        "metabolic_weight": metabolic_weight,
        "mechanical_weight": mechanical_weight,
        "formula": "metabolic*metabolic_weight + mechanical*mechanical_weight",
    }


def mountain_mechanical_load(duration_s: float, elevation_gain_m: float | None, elevation_loss_m: float | None) -> float:
    """Backward-compatible wrapper for the v0.4 public helper.

    v0.5 uses ``terrain_load_profile`` internally because distance and durability
    are now first-class dimensions. This wrapper retains the old API for callers
    and tests that only provide duration/elevation.
    """

    profile = terrain_load_profile("trail_running", duration_s, None, elevation_gain_m, elevation_loss_m)
    return profile.mechanical_load if profile else 0.0
