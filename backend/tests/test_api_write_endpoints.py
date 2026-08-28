"""HTTP-level coverage for every write endpoint.

The suite had no tests that went through FastAPI, which is how a fully broken calendar
write path (audit rows containing a raw datetime, so every request 500'd) shipped with a
green build. Each test here asserts the status code a real caller would see.
"""
from datetime import date, datetime, timedelta, timezone

import pytest


def _iso(day: date, hour: int = 18) -> str:
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).replace(hour=hour).isoformat()


@pytest.fixture()
def tomorrow():
    return date.today() + timedelta(days=1)


def test_health(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["version"]


def test_planned_workout_lifecycle_never_500s(client, tomorrow):
    created = client.post("/api/v1/planned-workouts", json={
        "scheduled_at": _iso(tomorrow), "sport": "running", "name": "Long run",
        "duration_s": 7200, "intensity": "endurance",
    })
    assert created.status_code == 200, created.text
    workout_id = created.json()["id"]
    assert created.json()["projected_load"] > 0

    patched = client.patch(f"/api/v1/planned-workouts/{workout_id}", json={"duration_s": 5400})
    assert patched.status_code == 200, patched.text
    assert patched.json()["projected_load"] < created.json()["projected_load"]

    matched = client.post(f"/api/v1/planned-workouts/{workout_id}/match", json={"activity_id": None})
    assert matched.status_code == 200, matched.text

    assert client.get(f"/api/v1/planned-workouts/{workout_id}/why").status_code == 200
    assert client.delete(f"/api/v1/planned-workouts/{workout_id}").status_code == 204


def test_every_write_records_a_readable_audit_row(client, tomorrow):
    """Regression: audit state is a JSON column, so it must contain no datetimes."""
    created = client.post("/api/v1/planned-workouts", json={
        "scheduled_at": _iso(tomorrow), "sport": "running", "name": "Audit me",
        "duration_s": 3600, "intensity": "easy",
    })
    assert created.status_code == 200, created.text
    client.patch(f"/api/v1/planned-workouts/{created.json()['id']}", json={"name": "Renamed"})

    audit = client.get("/api/v1/plan-audit")
    assert audit.status_code == 200
    rows = audit.json()
    assert [r["action"] for r in rows][:2] == ["update", "create"]
    after = rows[0]["after_state"]
    assert isinstance(after["scheduled_at"], str)


def test_objective_and_block_lifecycle(client):
    objective = client.post("/api/v1/objectives", json={
        "name": "Target race", "event_date": (date.today() + timedelta(days=90)).isoformat(),
        "sport": "trail_running", "priority": "A",
    })
    assert objective.status_code == 200, objective.text
    objective_id = objective.json()["id"]

    block = client.post("/api/v1/training-blocks", json={
        "name": "Base", "block_type": "base", "start_date": date.today().isoformat(),
        "end_date": (date.today() + timedelta(days=28)).isoformat(), "objective_id": objective_id,
    })
    assert block.status_code == 200, block.text

    assert client.patch(f"/api/v1/training-blocks/{block.json()['id']}", json={"name": "Base 2"}).status_code == 200
    assert client.delete(f"/api/v1/training-blocks/{block.json()['id']}").status_code == 204
    assert client.delete(f"/api/v1/objectives/{objective_id}").status_code == 204


def test_deleting_an_objective_detaches_its_workouts(client, session, tomorrow):
    """Regression: the delete used to violate a foreign key on Postgres and orphan the
    reference on SQLite."""
    from app.domain.models import PlannedWorkout

    objective_id = client.post("/api/v1/objectives", json={
        "name": "Race", "event_date": (date.today() + timedelta(days=60)).isoformat(), "sport": "running",
    }).json()["id"]
    workout_id = client.post("/api/v1/planned-workouts", json={
        "scheduled_at": _iso(tomorrow), "sport": "running", "name": "Tune-up",
        "duration_s": 3600, "intensity": "easy", "objective_id": objective_id,
    }).json()["id"]

    assert client.delete(f"/api/v1/objectives/{objective_id}").status_code == 204
    session.expire_all()
    assert session.get(PlannedWorkout, workout_id).objective_id is None


def test_locked_workout_requires_explicit_unlock(client, tomorrow):
    workout = client.post("/api/v1/planned-workouts", json={
        "scheduled_at": _iso(tomorrow), "sport": "running", "name": "Key session",
        "duration_s": 7200, "intensity": "threshold", "locked": True,
    }).json()
    later = _iso(tomorrow + timedelta(days=2))

    assert client.patch(f"/api/v1/planned-workouts/{workout['id']}", json={"scheduled_at": later}).status_code == 409
    assert client.delete(f"/api/v1/planned-workouts/{workout['id']}").status_code == 409
    # Documented escape hatch: unlock and edit in one deliberate request.
    unlocked = client.patch(f"/api/v1/planned-workouts/{workout['id']}", json={"scheduled_at": later, "locked": False})
    assert unlocked.status_code == 200
    assert unlocked.json()["locked"] is False


def test_planning_constraint_blocks_a_conflicting_workout(client, tomorrow):
    created = client.post("/api/v1/planning-constraints", json={
        "constraint_type": "unavailable", "start_date": tomorrow.isoformat(),
        "end_date": tomorrow.isoformat(), "value": {"hard": True},
    })
    assert created.status_code == 200, created.text
    blocked = client.post("/api/v1/planned-workouts", json={
        "scheduled_at": _iso(tomorrow), "sport": "running", "name": "Should be rejected",
        "duration_s": 3600, "intensity": "easy",
    })
    assert blocked.status_code == 409


def test_coach_endpoints(client):
    assert client.put("/api/v1/coach/profile", json={
        "available_hours_per_week": 8, "preferred_long_day": 5, "preferred_rest_day": 0,
        "doubles_allowed": False, "preferences": {},
    }).status_code == 200
    assert client.get("/api/v1/coach/context").status_code == 200

    asked = client.post("/api/v1/coach/ask", json={"question": "Why am I tired this week?"})
    assert asked.status_code == 200, asked.text
    assert asked.json()["answer"]
    assert client.get("/api/v1/coach/messages").status_code == 200


def test_generate_week_produces_an_approvable_proposal(client):
    client.post("/api/v1/objectives", json={
        "name": "Marathon", "event_date": (date.today() + timedelta(days=120)).isoformat(),
        "sport": "running", "priority": "A",
    })
    proposal = client.post("/api/v1/coach/generate-week", json={"strategy": "balanced"})
    assert proposal.status_code == 200, proposal.text
    body = proposal.json()
    assert body["status"] == "pending"
    assert body["validation"]["valid"] is True
    assert body["commands"]

    approved = client.post(f"/api/v1/coach/proposals/{body['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["proposal"]["status"] == "applied"
    assert len(approved.json()["applied"]) == len(body["commands"])
    # A decided proposal cannot be decided again.
    assert client.post(f"/api/v1/coach/proposals/{body['id']}/reject").status_code == 409


def test_adapt_week_and_reject(client):
    proposal = client.post("/api/v1/coach/adapt-week", json={})
    assert proposal.status_code == 200, proposal.text
    assert client.post(f"/api/v1/coach/proposals/{proposal.json()['id']}/reject").status_code == 200


def test_auto_match_runs_against_mixed_timezone_rows(client, session, athlete):
    """Regression: SQLite returns naive datetimes, which used to raise TypeError inside
    the matcher."""
    from app.domain.models import Activity
    from app.services.dedup import activity_fingerprint

    scheduled = datetime.now(timezone.utc) - timedelta(days=1)
    client.post("/api/v1/planned-workouts", json={
        "scheduled_at": scheduled.isoformat(), "sport": "running", "name": "Yesterday",
        "duration_s": 3600, "intensity": "easy",
    })
    session.add(Activity(
        athlete_id=athlete.id, sport="running", name="Actual", start_time=scheduled + timedelta(minutes=15),
        duration_s=3550, distance_m=10000,
        fingerprint=activity_fingerprint("running", scheduled, 3550, 10000),
    ))
    session.commit()

    result = client.post("/api/v1/matching/auto")
    assert result.status_code == 200, result.text
    assert result.json()["matched"] == 1


def test_upload_rejects_unsupported_files_without_leaving_staged_copies(client):
    from pathlib import Path

    from app.core.config import settings

    rejected = client.post("/api/v1/imports/files", files={"files": ("notes.txt", b"nope", "text/plain")})
    assert rejected.status_code == 400
    staging = Path(settings.storage_path) / "staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_analytics_endpoints(client):
    assert client.get("/api/v1/analytics/fitness").status_code == 200
    assert client.get("/api/v1/analytics/weekly?weeks=4").status_code == 200
    assert client.get("/api/v1/analytics/projection").status_code == 200
    assert client.get(f"/api/v1/calendar?start={date.today()}&end={date.today()}").status_code == 200


def test_invalid_load_kind_is_rejected_even_with_no_data(client):
    """Regression: validation lived inside the row loop, so an empty range returned 200
    with a plausible-looking series."""
    empty = client.get("/api/v1/analytics/fitness?load_kind=nonsense&start=2000-01-01&end=2000-01-05")
    assert empty.status_code == 422
    for kind in ("overall", "metabolic", "mechanical", "ascent", "descent", "durability"):
        assert client.get(f"/api/v1/analytics/fitness?load_kind={kind}").status_code == 200


def test_malformed_strava_webhooks_are_accepted_not_retried(client):
    """Strava retries any non-2xx, so an unparseable payload must not raise."""
    for payload in ({}, {"owner_id": None}, {"owner_id": 1}, {"owner_id": 1, "object_type": "activity"}):
        assert client.post("/api/v1/strava/webhook", json=payload).status_code == 200
    assert client.post("/api/v1/strava/webhook", content=b"not json").status_code == 200


def test_webhook_verification_requires_the_configured_token(client):
    assert client.get("/api/v1/strava/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "abc",
    }).status_code == 403


def test_activity_classification_override_recalculates_metrics(client, session, athlete):
    from app.domain.models import Activity, ActivityMetrics
    from app.services.dedup import activity_fingerprint

    start = datetime.now(timezone.utc) - timedelta(days=3)
    activity = Activity(
        athlete_id=athlete.id, sport="running", name="Hilly", start_time=start,
        duration_s=7200, distance_m=18000, elevation_gain_m=1200, elevation_loss_m=1200,
        fingerprint=activity_fingerprint("running", start, 7200, 18000),
    )
    session.add(activity)
    session.flush()
    session.add(ActivityMetrics(activity_id=activity.id, training_load=0.0, details={}))
    session.commit()

    response = client.patch(f"/api/v1/activities/{activity.id}/classification", json={"sport": "trail_running"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sport"] == "trail_running"
    assert body["classification"]["source"] == "manual"
    # Trail weights terrain more heavily, so the composite load must move.
    assert body["training_load"] > 0
