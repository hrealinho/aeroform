"""Resetting plans and deleting target races.

A plan is a graph - objective owns blocks, blocks own workouts, and the race itself is a
locked workout the planner created - so partial deletion produces the states that are
hardest to reason about.
"""
from datetime import date, datetime, timedelta, timezone

from app.domain.models import Objective, PlanChangeAudit, PlannedWorkout, TrainingBlock


def _objective(client, days=120, name="Target race"):
    return client.post("/api/v1/objectives", json={
        "name": name, "event_date": (date.today() + timedelta(days=days)).isoformat(),
        "sport": "running", "priority": "A", "distance_m": 42195,
    }).json()


def _workout(session, athlete_id, days_offset, locked=False, matched=None, objective_id=None, block_id=None):
    workout = PlannedWorkout(
        athlete_id=athlete_id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=days_offset),
        sport="running", name=f"session {days_offset:+d}", duration_s=3600,
        projected_load=50, steps=[{"type": "main", "intensity": "easy", "duration_s": 3600}],
        locked=locked, matched_activity_id=matched, objective_id=objective_id, block_id=block_id,
    )
    session.add(workout)
    session.commit()
    return workout


# --- resetting the plan ----------------------------------------------------

def test_reset_defaults_to_the_future_and_keeps_history(client, session, athlete):
    """A reset must not erase the record of what was prescribed for training already done."""
    past = _workout(session, athlete.id, -10)
    future = _workout(session, athlete.id, +10)

    body = client.delete("/api/v1/plan").json()
    assert body["workouts_deleted"] == 1
    assert body["scope"] == "future"
    session.expunge_all()
    assert session.get(PlannedWorkout, past.id) is not None
    assert session.get(PlannedWorkout, future.id) is None


def test_scope_all_removes_past_workouts_too(client, session, athlete):
    past = _workout(session, athlete.id, -10)
    _workout(session, athlete.id, +10)

    body = client.delete("/api/v1/plan?scope=all").json()
    assert body["workouts_deleted"] == 2
    assert body["note"] is None
    session.expunge_all()
    assert session.get(PlannedWorkout, past.id) is None


def test_matched_workouts_are_kept_by_default(client, session, athlete):
    """A matched workout is paired with a completed activity; deleting it loses the only
    record that the session was prescribed."""
    from app.domain.models import Activity
    from app.services.dedup import activity_fingerprint

    start = datetime.now(timezone.utc) + timedelta(days=3)
    activity = Activity(athlete_id=athlete.id, sport="running", name="done", start_time=start,
                        duration_s=3600, distance_m=10000,
                        fingerprint=activity_fingerprint("running", start, 3600, 10000))
    session.add(activity)
    session.commit()
    matched = _workout(session, athlete.id, +3, matched=activity.id)

    body = client.delete("/api/v1/plan").json()
    assert body["matched_skipped"] == 1
    session.expunge_all()
    assert session.get(PlannedWorkout, matched.id) is not None


def test_locked_workouts_need_an_explicit_flag(client, session, athlete):
    locked = _workout(session, athlete.id, +5, locked=True)

    kept = client.delete("/api/v1/plan").json()
    assert kept["locked_skipped"] == 1
    session.expunge_all()
    assert session.get(PlannedWorkout, locked.id) is not None

    removed = client.delete("/api/v1/plan?include_locked=true").json()
    assert removed["workouts_deleted"] == 1
    session.expunge_all()
    assert session.get(PlannedWorkout, locked.id) is None


def test_reset_removes_blocks_and_audits_what_went(client, session, athlete):
    _objective(client)
    client.post("/api/v1/coach/plan-season", json={"apply": True})
    assert session.query(TrainingBlock).count() > 0

    body = client.delete("/api/v1/plan?scope=all&include_locked=true").json()
    assert body["blocks_deleted"] > 0
    assert session.query(TrainingBlock).count() == 0

    audit = session.query(PlanChangeAudit).filter_by(entity_type="plan_reset").one()
    assert audit.before_state["workouts_deleted"] == body["workouts_deleted"]
    assert audit.reason.startswith("Plan reset")


def test_reset_can_be_scoped_to_one_objective(client, session, athlete):
    first = _objective(client, days=120, name="Race A")
    second = _objective(client, days=200, name="Race B")
    a = _workout(session, athlete.id, +5, objective_id=first["id"])
    b = _workout(session, athlete.id, +5, objective_id=second["id"])

    client.delete(f"/api/v1/plan?objective_id={first['id']}")
    session.expunge_all()
    assert session.get(PlannedWorkout, a.id) is None
    assert session.get(PlannedWorkout, b.id) is not None


def test_a_reset_with_nothing_to_remove_is_not_an_error(client):
    body = client.delete("/api/v1/plan").json()
    assert body["workouts_deleted"] == 0
    assert body["blocks_deleted"] == 0


# --- deleting the target race ---------------------------------------------

def test_deleting_a_race_detaches_its_plan_by_default(client, session, athlete):
    """Useful when swapping one race for another on the same build."""
    objective = _objective(client)
    workout = _workout(session, athlete.id, +5, objective_id=objective["id"])

    body = client.delete(f"/api/v1/objectives/{objective['id']}").json()
    assert body["with_plan"] is False
    assert body["workouts_deleted"] == 0
    session.expunge_all()
    survivor = session.get(PlannedWorkout, workout.id)
    assert survivor is not None
    assert survivor.objective_id is None


def test_deleting_a_race_with_its_plan_removes_the_locked_race_workout(client, session, athlete):
    """Regression: the planner creates the race locked, and delete_planned refused locked
    workouts unconditionally, so a race workout could never be removed."""
    objective = _objective(client)
    client.post("/api/v1/coach/plan-season", json={"apply": True})
    race = session.query(PlannedWorkout).filter_by(objective_id=objective["id"], locked=True).one()

    body = client.delete(f"/api/v1/objectives/{objective['id']}?with_plan=true").json()
    assert body["with_plan"] is True
    assert body["objectives_deleted"] == 1
    session.expunge_all()
    assert session.get(PlannedWorkout, race.id) is None
    assert session.get(Objective, objective["id"]) is None
    assert session.query(TrainingBlock).count() == 0


def test_deleting_a_race_is_audited(client, session, athlete):
    objective = _objective(client, name="Lisbon Marathon")
    client.delete(f"/api/v1/objectives/{objective['id']}")
    audit = session.query(PlanChangeAudit).filter_by(entity_type="objective", action="delete").one()
    assert audit.before_state["name"] == "Lisbon Marathon"


def test_deleting_a_missing_race_is_a_404(client):
    assert client.delete("/api/v1/objectives/999999").status_code == 404


# --- the locked-workout escape hatch --------------------------------------

def test_a_locked_workout_needs_force_to_delete(client, session, athlete):
    locked = _workout(session, athlete.id, +5, locked=True)
    assert client.delete(f"/api/v1/planned-workouts/{locked.id}").status_code == 409
    assert client.delete(f"/api/v1/planned-workouts/{locked.id}?force=true").status_code == 204
    session.expunge_all()
    assert session.get(PlannedWorkout, locked.id) is None


def test_replanning_after_a_reset_works(client, session, athlete):
    """The whole point: reset, then build again without tripping over leftovers."""
    _objective(client)
    client.post("/api/v1/coach/plan-season", json={"apply": True})
    client.delete("/api/v1/plan?scope=all&include_locked=true")

    again = client.post("/api/v1/coach/plan-season", json={"apply": True})
    assert again.status_code == 200
    assert session.query(TrainingBlock).count() == len(again.json()["blocks"])
