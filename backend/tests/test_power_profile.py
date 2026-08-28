"""Power-duration curve, critical power, rider type and race prediction."""
from datetime import datetime, timedelta, timezone

import pytest

from app.metrics.power_profile import CURVE_DURATIONS_S, build_curve, critical_power, mean_maximal, rider_type
from app.metrics.power_profile import CurvePoint
from app.metrics.race_predictor import BestEffort, RACE_DISTANCES, _hms, _pace, riegel


def _stream(segments: list[tuple[int, float]], start: datetime | None = None) -> list[dict]:
    """Build a 1 Hz power stream from (seconds, watts) segments."""
    start = start or datetime(2026, 5, 1, tzinfo=timezone.utc)
    samples, offset = [], 0
    for seconds, watts in segments:
        for _ in range(seconds):
            samples.append({"time": (start + timedelta(seconds=offset)).isoformat(), "power": watts, "speed": 8.0})
            offset += 1
    return samples


# --- mean maximal ----------------------------------------------------------

def test_mean_maximal_finds_the_hard_segment():
    samples = _stream([(600, 200), (300, 350), (600, 200)])
    curve = mean_maximal(samples, "power")
    assert curve["300"] == pytest.approx(350.0, abs=1.0)
    # A 20-minute window has to include easier riding, so it must be lower.
    assert curve["1200"] < curve["300"]


def test_mean_maximal_skips_windows_longer_than_the_activity():
    curve = mean_maximal(_stream([(300, 250)]), "power")
    assert "300" in curve
    assert "1200" not in curve
    assert "3600" not in curve


def test_mean_maximal_is_monotonically_non_increasing():
    """A longer window can never yield a higher mean than a shorter one."""
    samples = _stream([(120, 400), (600, 220), (300, 320), (900, 180)])
    curve = mean_maximal(samples, "power")
    values = [curve[str(d)] for d in CURVE_DURATIONS_S if str(d) in curve]
    assert values == sorted(values, reverse=True)


def test_mean_maximal_handles_no_stream():
    assert mean_maximal([], "power") == {}
    assert mean_maximal(_stream([(60, 200)]), "heartrate") == {}


# --- critical power --------------------------------------------------------

def _curve(pairs: list[tuple[int, float]]) -> list[CurvePoint]:
    return [CurvePoint(d, w, 1, "2026-05-01") for d, w in pairs]


def test_critical_power_recovers_a_known_model():
    """Generate from P = W'/t + CP and check the fit returns the inputs."""
    cp_true, w_prime = 250.0, 20000.0
    curve = _curve([(t, cp_true + w_prime / t) for t in (120, 300, 600, 1200)])
    fit = critical_power(curve)
    assert fit is not None
    assert fit["critical_power_w"] == pytest.approx(cp_true, abs=1.0)
    assert fit["w_prime_j"] == pytest.approx(w_prime, rel=0.02)
    assert fit["confidence"] == "medium"


def test_critical_power_needs_two_points_in_band():
    assert critical_power(_curve([(300, 320)])) is None
    # 5s and 3h are outside the 2-20 minute band the model holds in.
    assert critical_power(_curve([(5, 900), (10800, 190)])) is None


def test_critical_power_reports_low_confidence_on_thin_evidence():
    fit = critical_power(_curve([(300, 320), (600, 290)]))
    assert fit is not None and fit["confidence"] == "low"
    assert fit["caveat"]


# --- rider type ------------------------------------------------------------

@pytest.mark.parametrize(("sprint", "vo2", "threshold", "expected"), [
    (900, 300, 250, "Sprinter"),
    (520, 300, 282, "Time triallist"),
    (500, 300, 260, "Climber"),
    (660, 300, 255, "All-rounder"),
])
def test_rider_type_reads_curve_shape(sprint, vo2, threshold, expected):
    result = rider_type(_curve([(15, sprint), (300, vo2), (1200, threshold)]))
    assert result is not None
    assert result["type"] == expected
    assert result["caveat"]


def test_rider_type_needs_a_five_minute_anchor():
    assert rider_type(_curve([(15, 900)])) is None
    assert rider_type([]) is None


# --- curve aggregation over stored activities ------------------------------

def test_curve_takes_the_best_across_activities(client, session, athlete):
    from app.domain.models import Activity, ActivityMetrics
    from app.services.dedup import activity_fingerprint

    for index, (peak, days_ago) in enumerate([(320.0, 10), (405.0, 40), (280.0, 400)]):
        start = datetime.now(timezone.utc) - timedelta(days=days_ago)
        activity = Activity(
            athlete_id=athlete.id, sport="cycling", name=f"ride {index}", start_time=start,
            duration_s=1800, moving_time_s=1800, distance_m=20000,
            fingerprint=activity_fingerprint("cycling", start, 1800, 20000 + index),
        )
        session.add(activity)
        session.flush()
        session.add(ActivityMetrics(activity_id=activity.id, training_load=50.0,
                                    details={"mean_max_power": {"60": peak, "300": peak - 40}}))
    session.commit()

    year = build_curve(session, athlete.id, "power", days=365)
    best_60 = next(p for p in year if p.duration_s == 60)
    assert best_60.value == 405.0            # best within the year
    assert build_curve(session, athlete.id, "power", days=20)[0].value == 320.0
    # The 400-day-old ride is outside the year window.
    assert all(p.value != 280.0 for p in year)


def test_power_profile_endpoint_reports_missing_data(client):
    body = client.get("/api/v1/power/profile").json()
    assert body["has_data"] is False
    assert "STRAVA_SYNC_STREAMS" in body["advice"]
    assert "weight_advice" in body


def test_power_profile_uses_weight_for_w_per_kg(client, session, athlete):
    from app.domain.models import Activity, ActivityMetrics
    from app.services.dedup import activity_fingerprint

    start = datetime.now(timezone.utc) - timedelta(days=5)
    activity = Activity(athlete_id=athlete.id, sport="cycling", name="ride", start_time=start,
                        duration_s=1800, moving_time_s=1800, distance_m=20000,
                        fingerprint=activity_fingerprint("cycling", start, 1800, 20000))
    session.add(activity)
    session.flush()
    session.add(ActivityMetrics(activity_id=activity.id, training_load=50.0,
                               details={"mean_max_power": {"300": 285.0}}))
    session.commit()

    # recompute=false: a recompute would legitimately clear the injected curve, because
    # this activity has no stream to derive one from.
    assert client.post("/api/v1/thresholds/manual", json={
        "sport": "global", "metric": "weight_kg", "value": 57, "recompute": False,
    }).status_code == 200
    body = client.get("/api/v1/power/profile").json()
    assert body["weight_kg"] == 57
    assert body["points"][0]["w_per_kg"] == pytest.approx(5.0, abs=0.01)
    assert "weight_advice" not in body


# --- race prediction -------------------------------------------------------

def test_riegel_matches_the_published_formula():
    effort = BestEffort(10000.0, 2820.0, 1, "2026-08-24", "running")  # 47:00
    assert riegel(effort, 5000.0) == pytest.approx(2820 * 0.5 ** 1.06, abs=1)
    assert riegel(effort, 42195.0) > riegel(effort, 21097.5) > effort.duration_s


def test_predictions_come_from_recorded_runs(client, session, athlete):
    from app.domain.models import Activity
    from app.services.dedup import activity_fingerprint

    start = datetime.now(timezone.utc) - timedelta(days=7)
    session.add(Activity(
        athlete_id=athlete.id, sport="running", name="10k", start_time=start,
        duration_s=2900, moving_time_s=2820, distance_m=10000,
        fingerprint=activity_fingerprint("running", start, 2900, 10000),
    ))
    session.commit()

    body = client.get("/api/v1/running/predictions").json()
    assert body["has_data"] is True
    by_distance = {p["distance"]: p for p in body["predictions"]}
    assert set(by_distance) == {label for label, _ in RACE_DISTANCES}
    # 10K is measured, so confident; the marathon is a 4.2x extrapolation.
    assert by_distance["10K"]["confidence"] == "high"
    assert by_distance["Marathon"]["confidence"] == "low"
    assert by_distance["Marathon"]["caveat"]
    assert by_distance["10K"]["riegel_time"] == "47:00"


def test_predictions_ignore_trail_running(client, session, athlete):
    from app.domain.models import Activity
    from app.services.dedup import activity_fingerprint

    start = datetime.now(timezone.utc) - timedelta(days=7)
    session.add(Activity(
        athlete_id=athlete.id, sport="trail_running", name="trail", start_time=start,
        duration_s=3600, moving_time_s=3600, distance_m=10000,
        fingerprint=activity_fingerprint("trail_running", start, 3600, 10000),
    ))
    session.commit()
    # Terrain makes raw pace useless as race evidence.
    assert client.get("/api/v1/running/predictions").json()["has_data"] is False


def test_time_and_pace_formatting():
    assert _hms(2820) == "47:00"
    assert _hms(3661) == "1:01:01"
    assert _pace(2820, 10000) == "4:42/km"
