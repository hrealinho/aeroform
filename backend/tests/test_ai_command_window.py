"""The command validator must cover every workout a proposal references."""
from datetime import date, datetime, timedelta, timezone

from app.ai.commands import validate_commands
from app.domain.models import PlannedWorkout


def _workout(session, athlete_id: int, days_ahead: int, locked: bool = False) -> PlannedWorkout:
    workout = PlannedWorkout(
        athlete_id=athlete_id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=days_ahead),
        sport="running", name=f"Session +{days_ahead}d", duration_s=3600,
        projected_load=40, steps=[{"type": "main", "intensity": "easy", "duration_s": 3600}],
        locked=locked,
    )
    session.add(workout)
    session.commit()
    return workout


def test_far_future_workout_can_be_updated(client, session, athlete):
    """The window was hardcoded to today+42d, so anything beyond it was reported as
    "not found for this athlete" even though it existed."""
    workout = _workout(session, athlete.id, days_ahead=280)
    result = validate_commands(session, athlete.id, [
        {"action": "update_workout", "workout_id": workout.id, "duration_s": 1800},
    ])
    assert result["valid"] is True, result["errors"]


def test_far_future_workout_can_be_moved_and_deleted(client, session, athlete):
    workout = _workout(session, athlete.id, days_ahead=200)
    target = (datetime.now(timezone.utc) + timedelta(days=205)).isoformat()
    assert validate_commands(session, athlete.id, [
        {"action": "move_workout", "workout_id": workout.id, "scheduled_at": target},
    ])["valid"] is True
    assert validate_commands(session, athlete.id, [
        {"action": "delete_workout", "workout_id": workout.id},
    ])["valid"] is True


def test_missing_workout_reports_not_found(client, session, athlete):
    result = validate_commands(session, athlete.id, [
        {"action": "update_workout", "workout_id": 999999, "duration_s": 1800},
    ])
    assert result["valid"] is False
    assert "was not found for this athlete" in result["errors"][0]


def test_another_athletes_workout_is_not_found(client, session, athlete):
    from app.domain.models import Athlete

    other = Athlete(email="other@example.com")
    session.add(other)
    session.commit()
    workout = _workout(session, other.id, days_ahead=5)

    result = validate_commands(session, athlete.id, [
        {"action": "update_workout", "workout_id": workout.id, "duration_s": 1800},
    ])
    assert result["valid"] is False
    assert "was not found for this athlete" in result["errors"][0]


def test_locked_and_past_workouts_stay_protected(client, session, athlete):
    locked = _workout(session, athlete.id, days_ahead=150, locked=True)
    locked_result = validate_commands(session, athlete.id, [
        {"action": "update_workout", "workout_id": locked.id, "duration_s": 1800},
    ])
    assert locked_result["valid"] is False
    assert "is locked" in locked_result["errors"][0]

    past = _workout(session, athlete.id, days_ahead=-5)
    past_result = validate_commands(session, athlete.id, [
        {"action": "update_workout", "workout_id": past.id, "duration_s": 1800},
    ])
    assert past_result["valid"] is False
    assert "past workout" in past_result["errors"][0]


def test_creating_in_the_past_is_rejected(client, session, athlete):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    result = validate_commands(session, athlete.id, [
        {"action": "create_workout", "scheduled_at": yesterday, "sport": "running",
         "name": "Backdated", "duration_s": 3600, "intensity": "easy"},
    ])
    assert result["valid"] is False
    assert "cannot create workouts in the past" in result["errors"][0]


def test_far_future_creation_is_simulated_not_rejected(client, session, athlete):
    target = (datetime.now(timezone.utc) + timedelta(days=300)).isoformat()
    result = validate_commands(session, athlete.id, [
        {"action": "create_workout", "scheduled_at": target, "sport": "running",
         "name": "Race week opener", "duration_s": 3600, "intensity": "easy"},
    ])
    assert result["valid"] is True, result["errors"]
    assert result["after_weeks"]
