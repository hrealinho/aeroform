"""Regressions for defects found by measuring against a real 2,767-activity history.

Each of these was invisible to code review and only showed up in the data.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.importers.common import ParsedActivity
from app.integrations.strava.mapper import estimate_elevation_loss, map_activity
from app.metrics.load import MAX_PLAUSIBLE_STOPPED_RATIO, resolve_durations, terrain_load_profile
from app.services.classification import classify_parsed


# --- 1. elapsed vs moving time -------------------------------------------------

def test_metabolic_duration_uses_moving_time():
    """Strava's normalized power and average HR describe moving time. Multiplying either
    by elapsed time inflated load by the whole stopped fraction."""
    d = resolve_durations(elapsed_s=10.8 * 3600, moving_time_s=4.4 * 3600)
    assert d.metabolic_s == pytest.approx(4.4 * 3600)
    assert d.basis == "moving_time"


def test_forgotten_stop_cannot_produce_absurd_load():
    """Real case: 72.6 h elapsed against 0.5 h moving produced a load of 8,920, roughly a
    quarter of a normal training year in one day."""
    d = resolve_durations(elapsed_s=72.6 * 3600, moving_time_s=0.5 * 3600)
    assert d.metabolic_s == pytest.approx(0.5 * 3600)
    assert d.durability_s == pytest.approx(0.5 * 3600 * MAX_PLAUSIBLE_STOPPED_RATIO)
    assert d.clamped is True


def test_time_on_feet_keeps_legitimate_breaks():
    """A long mountain day with real stops must keep its elapsed time on feet."""
    d = resolve_durations(elapsed_s=10 * 3600, moving_time_s=7 * 3600)
    assert d.durability_s == pytest.approx(10 * 3600)
    assert d.clamped is False


def test_durations_fall_back_to_elapsed_when_moving_is_unknown():
    d = resolve_durations(elapsed_s=3600, moving_time_s=None)
    assert d.metabolic_s == d.durability_s == 3600
    assert d.basis == "elapsed_only"
    assert resolve_durations(3600, 0).basis == "elapsed_only"


def test_moving_time_is_never_greater_than_elapsed():
    d = resolve_durations(elapsed_s=3600, moving_time_s=99999)
    assert d.metabolic_s <= 3600


def test_durability_load_reflects_clamped_duration():
    honest = terrain_load_profile("hiking", resolve_durations(10 * 3600, 7 * 3600).durability_s, 20000, 1200, 1200)
    absurd = terrain_load_profile("hiking", resolve_durations(72 * 3600, 1800).durability_s, 20000, 1200, 1200)
    assert honest.durability_load > absurd.durability_load


# --- 2. descent on summary-only sources ---------------------------------------

def test_closed_loop_estimates_loss_from_gain():
    """Strava summaries carry gain but never loss, so descent_load was 0 on every
    summary-imported activity and v0.5's descent weighting did nothing."""
    loss, provenance = estimate_elevation_loss(
        {"start_latlng": [40.0, -8.0], "end_latlng": [40.001, -8.001]}, gain=900.0
    )
    assert loss == 900.0
    assert provenance["elevation_loss_source"] == "estimated_closed_loop"
    assert provenance["elevation_loss_confidence"] == "medium"


def test_open_route_estimate_is_marked_low_confidence():
    loss, provenance = estimate_elevation_loss(
        {"start_latlng": [40.0, -8.0], "end_latlng": [40.5, -8.6]}, gain=900.0
    )
    assert loss == 900.0
    assert provenance["elevation_loss_source"] == "estimated_open_route"
    assert provenance["elevation_loss_confidence"] == "low"


def test_no_gain_means_no_invented_loss():
    assert estimate_elevation_loss({}, gain=None)[0] is None
    assert estimate_elevation_loss({}, gain=0.0)[0] is None


def test_streams_take_precedence_over_the_estimate():
    parsed = map_activity(
        {"id": 1, "sport_type": "TrailRun", "name": "x", "start_date": "2026-05-01T08:00:00Z",
         "elapsed_time": 600, "moving_time": 600, "total_elevation_gain": 100,
         "start_latlng": [40.0, -8.0], "end_latlng": [40.0, -8.0]},
        streams={
            "time": {"data": list(range(600))},
            "altitude": {"data": [100 + i * 0.5 for i in range(300)] + [250 - i * 0.1 for i in range(300)]},
        },
    )
    assert parsed.source_metadata["elevation_loss_source"] == "stream"


def test_summary_import_now_produces_descent_load():
    parsed = map_activity({
        "id": 2, "sport_type": "Hike", "name": "Loop", "start_date": "2026-05-01T08:00:00Z",
        "elapsed_time": 6 * 3600, "moving_time": 5 * 3600, "distance": 15000,
        "total_elevation_gain": 1100, "start_latlng": [40.0, -8.0], "end_latlng": [40.0, -8.0],
    })
    profile = terrain_load_profile(
        "hiking", resolve_durations(parsed.duration_s, parsed.moving_time_s).durability_s,
        parsed.distance_m, parsed.elevation_gain_m, parsed.elevation_loss_m,
    )
    assert profile.descent_load > 0
    assert profile.descent_load > profile.ascent_load  # hiking weights descent higher


# --- 6. trail classification ---------------------------------------------------

def _run(distance_m: float, gain_m: float, sport_type: str = "Run") -> ParsedActivity:
    return ParsedActivity(
        sport="trail_running" if sport_type == "TrailRun" else "running",
        subtype=sport_type, name="x", start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        duration_s=3600, moving_time_s=3600, distance_m=distance_m, elevation_gain_m=gain_m,
        source_metadata={"sport_type": sport_type, "classification_ambiguous": False},
    )


@pytest.mark.parametrize(("gain", "expected"), [(50, "running"), (200, "running"), (600, "trail_running")])
def test_road_run_is_refined_to_trail_only_on_real_vertical(gain, expected):
    """Strava labels most trail runs as plain "Run". Measured thresholds: road runs
    p95 = 21 m/km, activities Strava did label TrailRun start at p10 = 31 m/km."""
    result = classify_parsed(_run(10000, gain))
    assert result.sport == expected
    if expected == "trail_running":
        assert result.confidence == "medium"  # overridable


def test_short_steep_efforts_are_not_reclassified():
    assert classify_parsed(_run(2000, 200)).sport == "running"


def test_explicit_trailrun_is_never_second_guessed():
    result = classify_parsed(_run(10000, 20, sport_type="TrailRun"))
    assert result.sport == "trail_running"
    assert result.confidence == "high"


# --- 5. near-duplicate detection ----------------------------------------------

def _seed(session, athlete_id, start, distance, duration=3300, sport="running", name="Run"):
    from app.domain.models import Activity, ActivityMetrics
    from app.services.dedup import activity_fingerprint

    activity = Activity(
        athlete_id=athlete_id, sport=sport, name=name, start_time=start, duration_s=duration,
        moving_time_s=duration, distance_m=distance,
        fingerprint=activity_fingerprint(sport, start, duration, distance),
    )
    session.add(activity)
    session.flush()
    session.add(ActivityMetrics(activity_id=activity.id, training_load=60.0, details={}))
    session.commit()
    return activity


def test_same_session_uploaded_twice_is_surfaced(client, session, athlete):
    """Real case: 34 pairs with identical timestamps whose distance differed 1-4%, named
    as the same session in two languages. The 20 m fingerprint bucket missed them."""
    start = datetime.now(timezone.utc) - timedelta(days=5)
    _seed(session, athlete.id, start, 13675, name="Lunch Ride")
    _seed(session, athlete.id, start, 13778, name="Volta de bicicleta")

    body = client.get("/api/v1/activities/duplicates").json()
    assert body["group_count"] == 1
    assert body["duplicated_load"] == 60.0
    assert len(body["groups"][0]["activities"]) == 2


def test_genuinely_different_activities_are_not_flagged(client, session, athlete):
    start = datetime.now(timezone.utc) - timedelta(days=5)
    _seed(session, athlete.id, start, 13675)
    _seed(session, athlete.id, start + timedelta(hours=6), 13675)          # hours apart
    _seed(session, athlete.id, start, 13675, sport="cycling")              # different sport
    _seed(session, athlete.id, start, 40000)                               # different distance
    assert client.get("/api/v1/activities/duplicates").json()["group_count"] == 0


def test_duplicates_are_never_merged_automatically(client, session, athlete):
    from app.domain.models import Activity

    start = datetime.now(timezone.utc) - timedelta(days=5)
    a = _seed(session, athlete.id, start, 13675)
    b = _seed(session, athlete.id, start, 13778)
    client.get("/api/v1/activities/duplicates")
    # expunge_all, not expire_all: the row is deleted through the request's own session,
    # so a stale instance in this identity map would raise on refresh.
    session.expunge_all()
    assert session.get(Activity, a.id) is not None
    assert session.get(Activity, b.id) is not None

    # Resolution is an explicit athlete action.
    assert client.delete(f"/api/v1/activities/{b.id}").status_code == 204
    session.expunge_all()
    assert session.get(Activity, b.id) is None
    assert client.get("/api/v1/activities/duplicates").json()["group_count"] == 0
    assert client.delete(f"/api/v1/activities/{b.id}").status_code == 404


def test_deleting_a_matched_activity_keeps_the_planned_workout(client, session, athlete):
    from app.domain.models import PlannedWorkout

    start = datetime.now(timezone.utc) + timedelta(days=1)
    activity = _seed(session, athlete.id, start, 10000)
    workout = client.post("/api/v1/planned-workouts", json={
        "scheduled_at": start.isoformat(), "sport": "running", "name": "Session",
        "duration_s": 3300, "intensity": "easy",
    }).json()
    client.post(f"/api/v1/planned-workouts/{workout['id']}/match", json={"activity_id": activity.id})

    assert client.delete(f"/api/v1/activities/{activity.id}").status_code == 204
    session.expunge_all()
    survivor = session.get(PlannedWorkout, workout["id"])
    assert survivor is not None
    assert survivor.matched_activity_id is None


# --- 4. threshold advice -------------------------------------------------------

def test_thresholds_report_what_is_blocking_hr_load(client, session, athlete):
    """A summary-only history has no HR streams, so resting HR can never be inferred. The
    API must say so instead of silently leaving every activity on the RPE fallback."""
    _seed(session, athlete.id, datetime.now(timezone.utc) - timedelta(days=3), 10000)
    from app.domain.models import Activity

    activity = session.scalar(__import__("sqlalchemy").select(Activity))
    activity.avg_hr = 150
    session.commit()

    advice = client.get("/api/v1/thresholds").json()["advice"]
    by_metric = {a["metric"]: a for a in advice}
    assert by_metric["resting_hr"]["severity"] == "high"
    assert by_metric["resting_hr"]["auto_estimable"] is False   # no streams
    assert "resting_hr" in by_metric["resting_hr"]["action"]
    assert "streams" in by_metric


def test_advice_clears_once_the_threshold_exists(client, session, athlete):
    _seed(session, athlete.id, datetime.now(timezone.utc) - timedelta(days=3), 10000)
    from app.domain.models import Activity

    activity = session.scalar(__import__("sqlalchemy").select(Activity))
    activity.avg_hr = 150
    session.commit()

    client.post("/api/v1/thresholds/manual", json={"sport": "global", "metric": "resting_hr", "value": 48})
    client.post("/api/v1/thresholds/manual", json={"sport": "global", "metric": "max_hr", "value": 190})
    advice = {a["metric"] for a in client.get("/api/v1/thresholds").json()["advice"]}
    assert "resting_hr" not in advice
    assert "max_hr" not in advice


# --- activity detail and laps --------------------------------------------------

def test_activity_detail_returns_stats_zones_and_laps(client, session, athlete):
    from app.domain.models import Activity, ActivityLap, ActivityMetrics, ActivityStreams
    from app.services.dedup import activity_fingerprint

    start = datetime.now(timezone.utc) - timedelta(days=2)
    samples = [{"time": (start + timedelta(seconds=i)).isoformat(), "power": 200 + (i % 50),
                "hr": 140, "altitude": 100 + i * 0.1, "distance": float(i * 8)} for i in range(1200)]
    activity = Activity(
        athlete_id=athlete.id, sport="cycling", name="Ride", start_time=start,
        duration_s=1200, moving_time_s=1180, distance_m=9600, avg_power=225, avg_hr=140,
        fingerprint=activity_fingerprint("cycling", start, 1200, 9600),
    )
    session.add(activity)
    session.flush()
    session.add(ActivityStreams(activity_id=activity.id, samples=samples))
    session.add(ActivityMetrics(activity_id=activity.id, training_load=45.0, load_method="power",
                                details={"metabolic_load": 45.0, "hr_zones_s": {"z1": 300, "z2": 900}}))
    session.add(ActivityLap(activity_id=activity.id, lap_index=0, elapsed_s=600, moving_s=600,
                            distance_m=4800, avg_hr=138, avg_power=220, trigger="manual"))
    session.add(ActivityLap(activity_id=activity.id, lap_index=1, elapsed_s=600, moving_s=580,
                            distance_m=4800, avg_hr=142, avg_power=230, trigger="manual"))
    session.commit()

    body = client.get(f"/api/v1/activities/{activity.id}").json()
    assert body["summary"]["distance_m"] == 9600
    assert body["load"]["method"] == "power"
    assert len(body["laps"]) == 2
    assert body["laps"][0]["index"] == 0
    # Streams are downsampled before leaving the API.
    assert body["streams"]["sample_count"] == 1200
    assert 0 < len(body["streams"]["points"]) <= 600
    assert "power" in body["streams"]["channels"]
    zones = body["zones"]["hr"]
    assert [z["zone"] for z in zones] == ["Z1", "Z2"]
    assert zones[1]["pct"] == 75.0


def test_activity_detail_is_scoped_to_the_athlete(client, session, athlete):
    from app.domain.models import Activity, Athlete
    from app.services.dedup import activity_fingerprint

    other = Athlete(email="someone-else@example.com")
    session.add(other)
    session.commit()
    start = datetime.now(timezone.utc)
    foreign = Activity(athlete_id=other.id, sport="running", name="theirs", start_time=start,
                       duration_s=600, distance_m=2000,
                       fingerprint=activity_fingerprint("running", start, 600, 2000))
    session.add(foreign)
    session.commit()

    assert client.get(f"/api/v1/activities/{foreign.id}").status_code == 404
    assert client.get("/api/v1/activities/999999").status_code == 404


def test_duplicates_path_is_not_captured_by_the_id_route(client):
    """/activities/duplicates must not be parsed as an activity id."""
    assert client.get("/api/v1/activities/duplicates").status_code == 200


def test_downsample_preserves_shape_and_extremes():
    from app.services.activity_detail import downsample

    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    samples = [{"time": (base + timedelta(seconds=i)).isoformat(),
                "power": 400 if 500 <= i < 600 else 150,
                "altitude": 100 + (50 if 500 <= i < 600 else 0)} for i in range(2000)]
    points = downsample(samples, max_points=100)
    assert len(points) == 100
    assert max(p["power"] for p in points) > 300      # the surge survives
    assert max(p["altitude_max"] for p in points) == 150.0
    assert points[0]["t"] == 0
    assert points[-1]["t"] > 1800
    assert downsample([]) == []


def test_fit_and_tcx_lap_parsing_is_wired(monkeypatch):
    """Laps reach ParsedActivity, which is what ingestion persists."""
    from app.importers.common import ParsedActivity, ParsedLap

    parsed = ParsedActivity(
        sport="running", subtype=None, name="x",
        start_time=datetime(2026, 5, 1, tzinfo=timezone.utc), duration_s=600,
        laps=[ParsedLap(index=0, elapsed_s=300, distance_m=1000)],
    )
    assert parsed.laps[0].distance_m == 1000
    assert ParsedActivity(sport="running", subtype=None, name="x",
                          start_time=datetime(2026, 5, 1, tzinfo=timezone.utc), duration_s=1).laps == []
