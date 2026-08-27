from __future__ import annotations
from collections import deque
from math import isfinite


def _numbers(samples: list[dict], key: str) -> list[float]:
    values = []
    for sample in samples:
        value = sample.get(key)
        if isinstance(value, (int, float)) and isfinite(float(value)):
            values.append(float(value))
    return values


def normalized_power(samples: list[dict], window: int = 30) -> float | None:
    """Approximate cycling/running normalized power for ~1 Hz activity streams."""
    watts = _numbers(samples, "power")
    if len(watts) < window:
        return None
    queue: deque[float] = deque()
    running_sum = 0.0
    rolling: list[float] = []
    for watt in watts:
        queue.append(watt)
        running_sum += watt
        if len(queue) > window:
            running_sum -= queue.popleft()
        if len(queue) == window:
            rolling.append(running_sum / window)
    if not rolling:
        return None
    fourth_mean = sum(v ** 4 for v in rolling) / len(rolling)
    return round(fourth_mean ** 0.25, 2)


def zone_seconds(samples: list[dict], key: str, boundaries: list[float]) -> dict[str, int]:
    """Count samples into zones. One sample is treated as roughly one second."""
    counts = {f"z{i + 1}": 0 for i in range(len(boundaries) + 1)}
    for sample in samples:
        value = sample.get(key)
        if not isinstance(value, (int, float)):
            continue
        idx = 0
        while idx < len(boundaries) and float(value) >= boundaries[idx]:
            idx += 1
        counts[f"z{idx + 1}"] += 1
    return counts


def power_zone_seconds(samples: list[dict], threshold_power: float | None) -> dict[str, int] | None:
    if not threshold_power or threshold_power <= 0:
        return None
    # Boundaries intentionally live in the versioned metrics layer, not in the UI.
    return zone_seconds(samples, "power", [0.55 * threshold_power, 0.75 * threshold_power, 0.90 * threshold_power, 1.05 * threshold_power, 1.20 * threshold_power, 1.50 * threshold_power])


def hr_zone_seconds(samples: list[dict], threshold_hr: float | None) -> dict[str, int] | None:
    if not threshold_hr or threshold_hr <= 0:
        return None
    return zone_seconds(samples, "hr", [0.85 * threshold_hr, 0.90 * threshold_hr, 0.95 * threshold_hr, 1.00 * threshold_hr])


def aerobic_decoupling(samples: list[dict]) -> float | None:
    """First-half vs second-half efficiency drift using power/HR, else speed/HR."""
    usable = [s for s in samples if isinstance(s.get("hr"), (int, float)) and s.get("hr", 0) > 0]
    if len(usable) < 120:
        return None
    metric_key = "power" if sum(isinstance(s.get("power"), (int, float)) for s in usable) > len(usable) * 0.6 else "speed"
    usable = [s for s in usable if isinstance(s.get(metric_key), (int, float)) and s.get(metric_key, 0) >= 0]
    if len(usable) < 120:
        return None
    mid = len(usable) // 2

    def ef(items: list[dict]) -> float | None:
        avg_metric = sum(float(s[metric_key]) for s in items) / len(items)
        avg_hr = sum(float(s["hr"]) for s in items) / len(items)
        return avg_metric / avg_hr if avg_hr > 0 else None

    first, second = ef(usable[:mid]), ef(usable[mid:])
    if not first or not second:
        return None
    # Positive number means efficiency worsened in the second half.
    return round((first - second) / first * 100.0, 2)
