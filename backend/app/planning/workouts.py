from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable


INTENSITY_FACTORS = {
    "recovery": 0.45,
    "easy": 0.58,
    "endurance": 0.68,
    "steady": 0.76,
    "tempo": 0.84,
    "threshold": 0.95,
    "vo2": 1.08,
    "anaerobic": 1.18,
    "race": 0.90,
}


@dataclass(frozen=True)
class WorkoutEstimate:
    load: float
    intensity_factor: float
    method: str = "planned_duration_intensity_v1"


def infer_intensity(steps: Iterable[dict] | None) -> str:
    """Infer the dominant intensity label from structured workout steps."""
    labels: list[str] = []
    for step in steps or []:
        label = str(step.get("intensity") or step.get("type") or "").lower()
        if label in INTENSITY_FACTORS:
            labels.append(label)
        for child in step.get("steps") or []:
            child_label = str(child.get("intensity") or child.get("type") or "").lower()
            if child_label in INTENSITY_FACTORS:
                labels.append(child_label)
    if not labels:
        return "endurance"
    priority = ["anaerobic", "vo2", "threshold", "tempo", "race", "steady", "endurance", "easy", "recovery"]
    return min(labels, key=lambda x: priority.index(x))


def estimate_planned_load(duration_s: float | None, steps: list[dict] | None = None, intensity: str | None = None) -> WorkoutEstimate:
    """Estimate a familiar TSS-like load for a planned workout.

    This is deliberately deterministic and modest: duration_hours * IF^2 * 100.
    It is not presented as measured physiological load. The method is versioned so
    it can later be replaced without rewriting the plan semantics silently.
    """
    duration_s = max(float(duration_s or 0), 0.0)
    label = (intensity or infer_intensity(steps)).lower()
    factor = INTENSITY_FACTORS.get(label, INTENSITY_FACTORS["endurance"])
    load = (duration_s / 3600.0) * (factor**2) * 100.0
    return WorkoutEstimate(load=round(load, 1), intensity_factor=factor)
