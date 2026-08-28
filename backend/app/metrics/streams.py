from __future__ import annotations
from collections import deque
from math import isfinite

from app.metrics.terrain import sample_durations


def _numbers(samples: list[dict], key: str) -> list[float]:
    values = []
    for sample in samples:
        value = sample.get(key)
        if isinstance(value, (int, float)) and isfinite(float(value)):
            values.append(float(value))
    return values


def normalized_power(samples: list[dict], window: int | None = None, window_s: float = 30.0) -> float | None:
    """Approximate cycling/running normalized power from a 30-second rolling mean.

    The window is expressed in seconds and converted using the stream's real cadence,
    so a 5-second-interval stream uses 6 samples rather than 30 (which would have
    averaged 2.5 minutes and understated variability).
    """
    watts = _numbers(samples, "power")
    if window is None:
        durations = sample_durations([s for s in samples if isinstance(s.get("power"), (int, float))])
        cadence = max(min(durations), 0.1) if durations else 1.0
        window = max(1, int(round(window_s / cadence)))
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


def zone_seconds(samples: list[dict], key: str, boundaries: list[float], total_duration_s: float | None = None) -> dict[str, float]:
    """Seconds spent in each zone.

    Durations come from the stream's own timestamps, so a 5-second GPX trace and a
    1 Hz FIT file of the same ride report the same totals. Counting one sample as one
    second understated every zone for sparsely sampled sources.
    """
    counts = {f"z{i + 1}": 0.0 for i in range(len(boundaries) + 1)}
    durations = sample_durations(samples, total_duration_s)
    for index, sample in enumerate(samples):
        value = sample.get(key)
        if not isinstance(value, (int, float)):
            continue
        idx = 0
        while idx < len(boundaries) and float(value) >= boundaries[idx]:
            idx += 1
        counts[f"z{idx + 1}"] += durations[index]
    return {k: round(v, 1) for k, v in counts.items()}


def power_zone_seconds(samples: list[dict], threshold_power: float | None, total_duration_s: float | None = None) -> dict[str, float] | None:
    if not threshold_power or threshold_power <= 0:
        return None
    # Boundaries intentionally live in the versioned metrics layer, not in the UI.
    return zone_seconds(samples, "power", [0.55 * threshold_power, 0.75 * threshold_power, 0.90 * threshold_power, 1.05 * threshold_power, 1.20 * threshold_power, 1.50 * threshold_power], total_duration_s)


def hr_zone_seconds(samples: list[dict], threshold_hr: float | None, total_duration_s: float | None = None) -> dict[str, float] | None:
    if not threshold_hr or threshold_hr <= 0:
        return None
    return zone_seconds(samples, "hr", [0.85 * threshold_hr, 0.90 * threshold_hr, 0.95 * threshold_hr, 1.00 * threshold_hr, 1.03 * threshold_hr, 1.06 * threshold_hr], total_duration_s)


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
