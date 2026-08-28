"""Regressions for the v0.5.2 correctness fixes.

Each test names the behaviour that was wrong, so a future change that reintroduces the
old shortcut fails here rather than silently degrading a metric.
"""
from datetime import date, datetime, timedelta, timezone

from app.metrics.fitness import ewma_series
from app.metrics.streams import zone_seconds
from app.metrics.terrain import elevation_gain_loss, moving_time_from_streams, sample_durations
from app.metrics.thresholds import rolling_best_average, rolling_lowest_average
from app.services.dedup import activity_fingerprint


def _climb(total_gain_m: float, seconds: int, interval_s: int) -> list[dict]:
    """Steady climb sampled every ``interval_s`` seconds."""
    steps = seconds // interval_s
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    return [
        {"altitude": 100 + i * (total_gain_m / steps), "time": (base + timedelta(seconds=i * interval_s)).isoformat()}
        for i in range(steps + 1)
    ]


# --- elevation -------------------------------------------------------------

def test_dense_streams_report_real_ascent():
    """A 1 Hz stream used to report zero gain: every per-sample delta of a 1000 m/h
    climb (0.28 m) fell under the noise floor and was discarded."""
    gain, loss = elevation_gain_loss(_climb(1000, 3600, 1))
    assert gain is not None and gain > 900
    assert loss == 0.0


def test_ascent_is_independent_of_sampling_rate():
    results = [elevation_gain_loss(_climb(1000, 3600, interval))[0] for interval in (1, 2, 5, 10)]
    assert all(r is not None and r > 900 for r in results)
    assert max(results) - min(results) < 60


def test_sensor_jitter_on_flat_ground_does_not_accumulate():
    """Hysteresis gates direction reversals, so sub-floor noise cancels instead of
    accumulating once a direction has been established."""
    jitter = [0.0, 0.5, -0.4, 0.3, -0.5, 0.4, -0.3, 0.2, -0.2, 0.45, -0.45] * 60
    samples = [{"altitude": 100 + offset} for offset in jitter]
    gain, loss = elevation_gain_loss(samples)
    assert gain is not None and gain < 5
    assert loss is not None and loss < 5


def test_real_rolling_terrain_is_measured():
    samples = []
    for _ in range(5):
        samples += [{"altitude": 100 + i * (40 / 200)} for i in range(200)]
        samples += [{"altitude": 140 - i * (40 / 200)} for i in range(200)]
    gain, loss = elevation_gain_loss(samples)
    assert 170 < gain < 210
    assert 170 < loss < 210


# --- stream durations ------------------------------------------------------

def test_zone_seconds_uses_real_sample_duration():
    """One sample is not one second. A 10-second-interval stream of 6 samples covers a
    minute, not six seconds."""
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    samples = [{"power": 200, "time": (base + timedelta(seconds=i * 10)).isoformat()} for i in range(6)]
    zones = zone_seconds(samples, "power", [150, 250])
    assert sum(zones.values()) == 60.0
    assert zones["z2"] == 60.0


def test_sample_durations_ignore_long_pauses():
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    samples = [{"time": (base + timedelta(seconds=i)).isoformat()} for i in range(10)]
    samples += [{"time": (base + timedelta(seconds=4000 + i)).isoformat()} for i in range(10)]
    durations = sample_durations(samples)
    # The multi-hour gap must not be charged as active time.
    assert sum(durations) < 60


def test_moving_time_excludes_stopped_samples():
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    moving = [{"time": (base + timedelta(seconds=i)).isoformat(), "speed": 3.0} for i in range(600)]
    stopped = [{"time": (base + timedelta(seconds=600 + i)).isoformat(), "speed": 0.0} for i in range(600)]
    assert moving_time_from_streams(moving + stopped) == 600.0


def test_rolling_best_average_measures_a_window_of_real_time():
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    # 10-second cadence: 120 samples span 20 minutes.
    samples = [{"power": 200, "time": (base + timedelta(seconds=i * 10)).isoformat()} for i in range(60)]
    samples += [{"power": 300, "time": (base + timedelta(seconds=600 + i * 10)).isoformat()} for i in range(60)]
    assert rolling_best_average(samples, "power", 600) == 300.0
    assert rolling_lowest_average(samples, "power", 600) == 200.0
    # A window longer than the stream cannot be measured.
    assert rolling_best_average(samples, "power", 7200) is None


# --- deduplication ---------------------------------------------------------

def test_fingerprint_is_independent_of_timezone_representation():
    """The same instant arriving as UTC, as a local offset, or naive after a SQLite
    round-trip must dedupe to one activity."""
    utc = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    variants = [utc, utc.astimezone(timezone(timedelta(hours=2))), utc.replace(tzinfo=None)]
    prints = {activity_fingerprint("running", value, 3600, 10000) for value in variants}
    assert len(prints) == 1


def test_fingerprint_still_separates_different_instants():
    utc = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    assert activity_fingerprint("running", utc, 3600, 10000) != activity_fingerprint(
        "running", utc + timedelta(hours=1), 3600, 10000
    )


# --- fitness model ---------------------------------------------------------

def test_ewma_warmup_makes_a_narrow_window_agree_with_a_wide_one():
    """Without warm-up, a zoomed chart started the EWMA at zero and reported fitness
    ramping up from nothing."""
    today = date(2026, 6, 30)
    load = {today - timedelta(days=i): 80.0 for i in range(400)}
    wide = ewma_series(load, today - timedelta(days=364), today)
    narrow = ewma_series(load, today, today, warmup_days=365)
    assert narrow[-1]["fitness"] == wide[-1]["fitness"]
    assert narrow[-1]["fitness"] > 50


def test_ewma_respects_configured_time_constants():
    today = date(2026, 6, 30)
    load = {today - timedelta(days=i): 80.0 for i in range(60)}
    fast = ewma_series(load, today, today, fitness_tau=7, warmup_days=60)
    slow = ewma_series(load, today, today, fitness_tau=90, warmup_days=60)
    assert fast[-1]["fitness"] > slow[-1]["fitness"]
