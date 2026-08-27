from datetime import date, timedelta
import zipfile

import pytest

from app.ai.analyst import analyse_question
from app.ai.planner import generate_week_seed
from app.domain.schemas import PlanCommand
from app.importers.zip_import import safe_members


def base_context():
    today = date(2026, 8, 27)
    return {
        "as_of": today.isoformat(),
        "history": {"activity_count": 300},
        "state": {"fitness": 70, "fatigue": 84, "form": -14, "load_7d": 640, "load_28d": 2200, "typical_weekly_load_8w": 500, "typical_weekly_hours_8w": 8},
        "sports_28d": {"running": {"load": 1200, "hours": 20, "elevation_m": 1200, "sessions": 18}},
        "objectives": [{"id": 1, "name": "Marathon", "date": "2026-12-01", "days_away": 96, "sport": "running", "priority": "A", "elevation_m": 0}],
        "current_block": {"id": 1, "name": "Build", "type": "build", "targets": {}},
        "constraints": [],
        "profile": {"available_hours_per_week": 8, "preferred_long_day": 5, "preferred_rest_day": 0, "doubles_allowed": False, "preferences": {}},
        "planned": [],
        "recent_activities": [],
        "recent_weeks": [],
        "recent_four_weeks": [],
        "prior_four_weeks": [],
        "adherence_28d_pct": None,
    }


def test_fatigue_analysis_uses_recent_vs_typical_load():
    result = analyse_question(base_context(), "Why am I tired this week?")
    assert "640" in result["answer"]
    assert any(e["metric"] == "typical weekly load" for e in result["evidence"])


def test_generate_week_returns_structured_valid_commands():
    context = base_context()
    summary, commands = generate_week_seed(context, date(2026, 8, 31), objective_id=1)
    assert commands
    assert "Marathon" in summary
    for command in commands:
        validated = PlanCommand.model_validate(command)
        assert validated.action == "create_workout"
        assert validated.scheduled_at.date() >= date(2026, 8, 31)


def test_generate_week_respects_unavailable_long_day():
    context = base_context()
    context["constraints"] = [{"id": 9, "type": "unavailable", "start_date": "2026-09-05", "end_date": "2026-09-05", "weekdays": [], "value": {"hard": True}}]
    _, commands = generate_week_seed(context, date(2026, 8, 31), objective_id=1)
    assert all(c["scheduled_at"][:10] != "2026-09-05" for c in commands)


def test_plan_command_rejects_delete_without_workout_id():
    with pytest.raises(ValueError):
        PlanCommand.model_validate({"action": "delete_workout"})


def test_strava_archive_gzip_members_and_macos_metadata(tmp_path):
    archive = tmp_path / "activities.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("activities/123.fit.gz", b"not parsed here")
        zf.writestr("activities/456.gpx", b"<gpx/>")
        zf.writestr("__MACOSX/activities/._123.fit.gz", b"resource fork")
        zf.writestr("activities/.DS_Store", b"metadata")
    members = list(safe_members(str(archive)))
    assert [m.filename for m in members] == ["activities/123.fit.gz", "activities/456.gpx"]


def test_command_validator_blocks_locked_workout():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.session import Base
    import app.domain.models  # noqa
    from app.domain.models import Athlete, PlannedWorkout
    from app.ai.commands import validate_commands
    from datetime import datetime

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        athlete = Athlete(email="locked@example.com")
        db.add(athlete); db.flush()
        tomorrow = date.today() + timedelta(days=1)
        workout = PlannedWorkout(athlete_id=athlete.id, scheduled_at=datetime.combine(tomorrow, datetime.min.time()), sport="running", name="Key run", duration_s=3600, projected_load=70, steps=[{"type":"main","intensity":"threshold","duration_s":3600}], locked=True)
        db.add(workout); db.commit()
        result = validate_commands(db, athlete.id, [{"action":"update_workout","workout_id":workout.id,"duration_s":1800,"intensity":"easy"}])
        assert result["valid"] is False
        assert any("locked" in error for error in result["errors"])


def test_apply_validated_ai_create_writes_audit():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.db.session import Base
    import app.domain.models  # noqa
    from app.domain.models import Athlete, PlannedWorkout, PlanChangeAudit
    from app.ai.commands import apply_commands
    from datetime import datetime

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        athlete = Athlete(email="apply@example.com")
        db.add(athlete); db.commit(); db.refresh(athlete)
        tomorrow = date.today() + timedelta(days=1)
        applied = apply_commands(db, athlete.id, [{
            "action":"create_workout", "scheduled_at":datetime.combine(tomorrow, datetime.min.time()).isoformat(),
            "sport":"running", "name":"Easy run", "duration_s":3600, "intensity":"easy", "reason":"AI test"
        }])
        assert len(applied) == 1
        assert db.scalar(select(PlannedWorkout).where(PlannedWorkout.athlete_id == athlete.id)) is not None
        audit = db.scalar(select(PlanChangeAudit).where(PlanChangeAudit.athlete_id == athlete.id))
        assert audit is not None
        assert audit.initiated_by == "ai"


def test_archive_rejects_windows_style_traversal(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(r"..\\escape.fit", b"x")
    with pytest.raises(ValueError, match="Unsafe archive path"):
        list(safe_members(str(archive)))
