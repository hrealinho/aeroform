"""End-to-end coverage for thresholds actually reaching stored load.

Before v0.5.2 there was no working path: the estimate backfill was never committed, and a
manual threshold defaulted to valid_from=today so it could not apply to any past activity.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain.models import Activity, ActivityMetrics, ActivityStreams, AthleteThreshold
from app.metrics.thresholds import estimate_thresholds
from app.services.dedup import activity_fingerprint


def _seed_ride(session, athlete_id: int, days_ago: int = 30, power: int = 240, hr: int = 155, max_hr: int = 172):
    """A 90-minute ride with a 1 Hz power/HR stream, preceded by a resting-HR warm-up."""
    start = datetime.now(timezone.utc) - timedelta(days=days_ago)
    seconds = 5400
    samples = [
        {"time": (start + timedelta(seconds=i)).isoformat(), "power": power, "hr": hr, "speed": 8.0}
        for i in range(seconds)
    ]
    samples[:120] = [{**s, "power": 0, "hr": 50, "speed": 0.0} for s in samples[:120]]
    activity = Activity(
        athlete_id=athlete_id, sport="cycling", name="Steady", start_time=start, duration_s=seconds,
        distance_m=seconds * 8, avg_power=power, avg_hr=hr, max_hr=max_hr,
        fingerprint=activity_fingerprint("cycling", start, seconds, seconds * 8),
    )
    session.add(activity)
    session.flush()
    session.add(ActivityStreams(activity_id=activity.id, samples=samples))
    session.add(ActivityMetrics(activity_id=activity.id, training_load=0.0, load_method="session_rpe", details={}))
    session.commit()
    return activity


def _metrics(session, activity_id: int):
    session.expire_all()
    return session.get(ActivityMetrics, activity_id)


def test_estimate_with_backfill_updates_stored_load(client, session, athlete):
    activity = _seed_ride(session, athlete.id)
    assert _metrics(session, activity.id).load_method == "session_rpe"

    response = client.post("/api/v1/thresholds/estimate", json={
        "history_days": 365, "persist": True, "apply_to_history": True,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["saved"] > 0
    assert body["recompute_task_id"] is not None
    assert body["effective_from"] <= (date.today() - timedelta(days=30)).isoformat()

    updated = _metrics(session, activity.id)
    assert updated.load_method == "power"
    assert updated.training_load > 0


def test_estimator_produces_resting_hr_so_hr_load_is_reachable(client, session, athlete):
    _seed_ride(session, athlete.id)
    metrics = {e.metric for e in estimate_thresholds(session, athlete.id)}
    # hr_trimp_load needs both; estimating only max_hr left HR-only activities on the
    # session-RPE fallback forever.
    assert {"max_hr", "resting_hr"} <= metrics


def test_manual_threshold_applies_to_existing_history(client, session, athlete):
    activity = _seed_ride(session, athlete.id, days_ago=20)
    for metric, value in (("max_hr", 175), ("resting_hr", 48)):
        response = client.post("/api/v1/thresholds/manual", json={
            "sport": "global", "metric": metric, "value": value,
        })
        assert response.status_code == 200, response.text
        assert response.json()["effective_from"] <= (date.today() - timedelta(days=20)).isoformat()

    # Power wins over HR when both are available, so remove the power threshold to
    # observe the HR path specifically.
    ftp = client.post("/api/v1/thresholds/manual", json={"sport": "cycling", "metric": "ftp", "value": 250}).json()
    assert _metrics(session, activity.id).load_method == "power"
    ftp_id = next(t["id"] for t in ftp["thresholds"] if t["metric"] == "ftp")
    assert client.delete(f"/api/v1/thresholds/{ftp_id}").status_code == 200
    assert _metrics(session, activity.id).load_method == "hr_trimp"


def test_repeated_manual_entry_replaces_rather_than_duplicates(client, session, athlete):
    _seed_ride(session, athlete.id)
    for value in (250, 260, 270):
        assert client.post("/api/v1/thresholds/manual", json={
            "sport": "cycling", "metric": "ftp", "value": value,
        }).status_code == 200

    rows = [r for r in session.scalars(
        __import__("sqlalchemy").select(AthleteThreshold)
    ) if r.metric == "ftp" and r.source == "manual"]
    assert len(rows) == 1
    assert rows[0].value == 270
    assert rows[0].valid_until is None


def test_manual_threshold_wins_over_an_estimate(client, session, athlete):
    _seed_ride(session, athlete.id)
    client.post("/api/v1/thresholds/estimate", json={"history_days": 365, "persist": True, "apply_to_history": True})
    client.post("/api/v1/thresholds/manual", json={"sport": "cycling", "metric": "ftp", "value": 300})

    listed = client.get("/api/v1/thresholds").json()["thresholds"]
    ftp = [t for t in listed if t["metric"] == "ftp"]
    assert len(ftp) == 1
    assert ftp[0]["source"] == "manual"
    assert ftp[0]["value"] == 300


def test_deleting_a_threshold_reverts_the_load_method(client, session, athlete):
    activity = _seed_ride(session, athlete.id)
    created = client.post("/api/v1/thresholds/manual", json={"sport": "cycling", "metric": "ftp", "value": 250}).json()
    assert _metrics(session, activity.id).load_method == "power"

    ftp_id = next(t["id"] for t in created["thresholds"] if t["metric"] == "ftp")
    assert client.delete(f"/api/v1/thresholds/{ftp_id}").status_code == 200
    assert _metrics(session, activity.id).load_method != "power"
    assert client.delete(f"/api/v1/thresholds/{ftp_id}").status_code == 404


@pytest.mark.parametrize(
    ("avg_hr", "max_hr", "expected"),
    [(155, 172, "high"), (140, 172, "medium"), (125, 172, "low")],
)
def test_power_threshold_confidence_follows_effort_evidence(session, athlete, client, avg_hr, max_hr, expected):
    """A best-60-minute mean only equals threshold if the effort was near-maximal. Rating
    a steady endurance ride as high confidence produced a systematically low FTP."""
    _seed_ride(session, athlete.id, hr=avg_hr, max_hr=max_hr)
    ftp = next(e for e in estimate_thresholds(session, athlete.id) if e.metric == "ftp")
    assert ftp.confidence == expected
    if expected == "low":
        assert ftp.evidence["caveat"]


def test_zones_are_exposed_for_each_threshold_kind(client, session, athlete):
    _seed_ride(session, athlete.id)
    for metric, value in (("ftp", 250), ("threshold_hr", 165), ("threshold_speed_mps", 4.1)):
        sport = "cycling" if metric == "ftp" else "running"
        client.post("/api/v1/thresholds/manual", json={"sport": sport, "metric": metric, "value": value})

    listed = client.get("/api/v1/thresholds").json()["thresholds"]
    by_metric = {t["metric"]: t for t in listed}
    assert len(by_metric["ftp"]["zones"]) == 7
    assert len(by_metric["threshold_hr"]["zones"]) == 7
    assert by_metric["threshold_speed_mps"]["display_value"].endswith("/km")
    assert by_metric["threshold_speed_mps"]["zones"][3]["faster_than"]
