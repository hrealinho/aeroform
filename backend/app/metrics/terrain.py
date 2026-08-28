from __future__ import annotations

from math import isfinite


def _value(sample: dict, key: str) -> float | None:
    v = sample.get(key)
    if isinstance(v, (int, float)) and isfinite(float(v)):
        return float(v)
    return None


def smoothed_elevation(samples: list[dict], window: int = 3) -> list[float]:
    """Small moving-average smoother to reduce GPS/barometric point noise."""
    raw = [_value(s, "altitude") for s in samples]
    values = [v for v in raw if v is not None]
    if not values:
        return []
    result: list[float] = []
    for idx, value in enumerate(raw):
        if value is None:
            continue
        lo = max(0, idx - window // 2)
        hi = min(len(raw), idx + window // 2 + 1)
        local = [v for v in raw[lo:hi] if v is not None]
        result.append(sum(local) / len(local))
    return result


def elevation_gain_loss(samples: list[dict], noise_floor_m: float = 0.75) -> tuple[float | None, float | None]:
    """Estimate gain/loss from a smoothed altitude stream.

    Very small point-to-point changes are ignored to avoid inflating total ascent
    on noisy GPX traces. This remains an estimate; device-provided FIT totals are
    preferred when present.
    """
    elevations = smoothed_elevation(samples)
    if len(elevations) < 2:
        return None, None
    gain = loss = 0.0
    for previous, current in zip(elevations, elevations[1:]):
        delta = current - previous
        if abs(delta) < noise_floor_m:
            continue
        if delta > 0:
            gain += delta
        else:
            loss += -delta
    return round(gain, 1), round(loss, 1)


def grade_distribution(samples: list[dict]) -> dict[str, int] | None:
    """Approximate seconds in terrain grade bands.

    Uses source-provided grade when present, otherwise derives grade from distance
    and altitude deltas. One sample interval is approximated as one second, matching
    the existing stream-zone semantics.
    """
    if not samples:
        return None
    counts = {"steep_down": 0, "down": 0, "flat": 0, "up": 0, "steep_up": 0}
    usable = 0
    previous = None
    for sample in samples:
        grade = _value(sample, "grade")
        if grade is None and previous is not None:
            d0, d1 = _value(previous, "distance"), _value(sample, "distance")
            a0, a1 = _value(previous, "altitude"), _value(sample, "altitude")
            if None not in (d0, d1, a0, a1) and d1 > d0:
                grade = (a1 - a0) / (d1 - d0) * 100.0
        previous = sample
        if grade is None or not isfinite(grade):
            continue
        usable += 1
        if grade < -8:
            counts["steep_down"] += 1
        elif grade < -3:
            counts["down"] += 1
        elif grade <= 3:
            counts["flat"] += 1
        elif grade <= 8:
            counts["up"] += 1
        else:
            counts["steep_up"] += 1
    return counts if usable else None


def terrain_stream_metrics(samples: list[dict], duration_s: float | None = None) -> dict:
    gain, loss = elevation_gain_loss(samples)
    hours = max(float(duration_s or 0), 0.0) / 3600.0
    return {
        "stream_elevation_gain_m": gain,
        "stream_elevation_loss_m": loss,
        "grade_distribution_s": grade_distribution(samples),
        "ascent_rate_m_per_h": round(gain / hours, 1) if gain is not None and hours > 0 else None,
        "descent_rate_m_per_h": round(loss / hours, 1) if loss is not None and hours > 0 else None,
    }
