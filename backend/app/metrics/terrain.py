from __future__ import annotations

from datetime import datetime
from math import isfinite

DEFAULT_SAMPLE_INTERVAL_S = 1.0
MAX_SAMPLE_GAP_S = 300.0


def _value(sample: dict, key: str) -> float | None:
    v = sample.get(key)
    if isinstance(v, (int, float)) and isfinite(float(v)):
        return float(v)
    return None


def _sample_time(sample: dict) -> datetime | None:
    raw = sample.get("time")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def sample_durations(samples: list[dict], total_duration_s: float | None = None) -> list[float]:
    """Seconds represented by each sample, derived from the stream's own timestamps.

    Stream sources are not all 1 Hz: FIT is usually 1 Hz, Strava velocity streams can
    be irregular, and GPX often records every few seconds. Counting one sample as one
    second silently mis-scales every zone total and best-effort window, so callers get
    real per-sample durations instead. Falls back to an even split of the known total
    duration, then to 1 Hz, when the stream carries no usable timestamps.
    """
    if not samples:
        return []
    times = [_sample_time(s) for s in samples]
    known = [t for t in times if t is not None]
    if len(known) < 2:
        if total_duration_s and total_duration_s > 0:
            return [float(total_duration_s) / len(samples)] * len(samples)
        return [DEFAULT_SAMPLE_INTERVAL_S] * len(samples)

    # Median gap is the representative cadence; it is used for the final sample and
    # wherever a pause makes the raw gap meaningless.
    gaps = []
    for previous, current in zip(known, known[1:]):
        delta = (current - previous).total_seconds()
        if 0 < delta <= MAX_SAMPLE_GAP_S:
            gaps.append(delta)
    gaps.sort()
    nominal = gaps[len(gaps) // 2] if gaps else DEFAULT_SAMPLE_INTERVAL_S

    durations: list[float] = []
    for index, current in enumerate(times):
        following = next((t for t in times[index + 1:] if t is not None), None)
        if current is None or following is None:
            durations.append(nominal)
            continue
        delta = (following - current).total_seconds()
        durations.append(delta if 0 < delta <= MAX_SAMPLE_GAP_S else nominal)
    return durations


def _smoothing_window(samples: list[dict], usable: int, window_s: float) -> int:
    """Number of samples covering roughly ``window_s`` of real time.

    Expressed in seconds rather than samples so a 1 Hz FIT stream and a 5-second GPX
    trace get equivalent smoothing. Capped at a third of the stream so short traces
    are not flattened into a straight line.
    """
    durations = sample_durations(samples)
    cadence = min(durations) if durations else DEFAULT_SAMPLE_INTERVAL_S
    cadence = max(cadence, 0.1)
    window = int(round(window_s / cadence))
    return max(1, min(window, max(1, usable // 3)))


def smoothed_elevation(samples: list[dict], window: int | None = None, window_s: float = 15.0) -> list[float]:
    """Moving-average smoother that spans a fixed amount of time, not a fixed sample count.

    GPS and barometric altitude carry sub-metre noise on every sample. Averaging over a
    time span means the amount of noise removed does not depend on how often the device
    happened to record.
    """
    raw = [_value(s, "altitude") for s in samples]
    values = [v for v in raw if v is not None]
    if not values:
        return []
    if window is None:
        window = _smoothing_window(samples, len(values), window_s)
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
    """Estimate gain/loss from a smoothed altitude stream using peak-to-valley hysteresis.

    The noise floor gates *direction reversals*, never individual point-to-point deltas.
    A per-sample gate would discard real climbing on dense streams - at 1 Hz a 1000 m/h
    ascent is only 0.28 m between samples, so every delta falls below the floor and the
    activity reports zero ascent. Here a monotonic run is banked in full when a reversal
    larger than the floor confirms it has ended, so the result is independent of sampling
    rate, while jitter cancels because it never clears the floor in one direction.

    Device-provided FIT totals remain preferred when present; this is an estimate.
    """
    elevations = smoothed_elevation(samples)
    if len(elevations) < 2:
        return None, None

    gain = loss = 0.0
    direction = 0  # +1 climbing, -1 descending, 0 not yet established
    pivot = extreme = elevations[0]
    low = high = elevations[0]

    for current in elevations[1:]:
        if direction == 0:
            # Undecided: remember the extremes so the first confirmed run is measured
            # from the true turning point rather than from wherever the stream started.
            low, high = min(low, current), max(high, current)
            if current - low >= noise_floor_m:
                direction, pivot, extreme = 1, low, current
            elif high - current >= noise_floor_m:
                direction, pivot, extreme = -1, high, current
        elif direction > 0:
            if current > extreme:
                extreme = current
            elif extreme - current >= noise_floor_m:
                gain += extreme - pivot
                direction, pivot, extreme = -1, extreme, current
        else:
            if current < extreme:
                extreme = current
            elif current - extreme >= noise_floor_m:
                loss += pivot - extreme
                direction, pivot, extreme = 1, extreme, current

    # Bank the run still in progress at the end of the activity.
    if direction > 0 and extreme > pivot:
        gain += extreme - pivot
    elif direction < 0 and extreme < pivot:
        loss += pivot - extreme

    return round(gain, 1), round(loss, 1)


def grade_distribution(samples: list[dict], total_duration_s: float | None = None) -> dict[str, float] | None:
    """Seconds in terrain grade bands.

    Uses source-provided grade when present, otherwise derives grade from distance
    and altitude deltas. Durations come from the stream's own timestamps rather than
    assuming one sample is one second.
    """
    if not samples:
        return None
    counts = {"steep_down": 0.0, "down": 0.0, "flat": 0.0, "up": 0.0, "steep_up": 0.0}
    durations = sample_durations(samples, total_duration_s)
    usable = 0
    previous = None
    for index, sample in enumerate(samples):
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
        seconds = durations[index]
        if grade < -8:
            counts["steep_down"] += seconds
        elif grade < -3:
            counts["down"] += seconds
        elif grade <= 3:
            counts["flat"] += seconds
        elif grade <= 8:
            counts["up"] += seconds
        else:
            counts["steep_up"] += seconds
    return {k: round(v, 1) for k, v in counts.items()} if usable else None


def moving_time_from_streams(samples: list[dict], stopped_speed_mps: float = 0.5) -> float | None:
    """Moving seconds derived from the stream, for sources that omit a moving-time summary.

    GPX never declares moving time, so elapsed time is the only thing available for
    speed-based sport classification. A run with a long stop then looks slow enough to
    be classified as a hike, which changes its load weighting.
    """
    if not samples:
        return None
    durations = sample_durations(samples)
    moving = 0.0
    counted = 0
    previous_distance = None
    previous_index = None
    for index, sample in enumerate(samples):
        speed = _value(sample, "speed")
        if speed is None:
            distance = _value(sample, "distance")
            if distance is not None and previous_distance is not None and previous_index is not None:
                elapsed = sum(durations[previous_index:index]) or durations[index]
                if elapsed > 0:
                    speed = max(0.0, distance - previous_distance) / elapsed
            if distance is not None:
                previous_distance, previous_index = distance, index
        if speed is None:
            continue
        counted += 1
        if speed >= stopped_speed_mps:
            moving += durations[index]
    return round(moving, 1) if counted else None


def terrain_stream_metrics(samples: list[dict], duration_s: float | None = None) -> dict:
    gain, loss = elevation_gain_loss(samples)
    hours = max(float(duration_s or 0), 0.0) / 3600.0
    return {
        "stream_elevation_gain_m": gain,
        "stream_elevation_loss_m": loss,
        "grade_distribution_s": grade_distribution(samples, duration_s),
        "stream_moving_time_s": moving_time_from_streams(samples),
        "ascent_rate_m_per_h": round(gain / hours, 1) if gain is not None and hours > 0 else None,
        "descent_rate_m_per_h": round(loss / hours, 1) if loss is not None and hours > 0 else None,
    }
