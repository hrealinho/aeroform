from __future__ import annotations
from copy import deepcopy
from datetime import date, datetime, timedelta
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActivityMetrics, PlannedWorkout, PlanningConstraint, PlanChangeAudit
from app.domain.schemas import PlanCommand
from app.planning.workouts import estimate_planned_load, infer_intensity
from app.planning.projection import project_load_series, projection_warnings, weekly_totals


def workout_dict(w: PlannedWorkout) -> dict:
    return {
        "id": w.id,
        "scheduled_at": w.scheduled_at.isoformat(),
        "sport": w.sport,
        "name": w.name,
        "duration_s": w.duration_s,
        "distance_m": w.distance_m,
        "elevation_m": w.elevation_m,
        "projected_load": w.projected_load,
        "steps": w.steps or [],
        "intensity": infer_intensity(w.steps),
        "locked": bool(w.locked),
        "objective_id": w.objective_id,
        "block_id": w.block_id,
        "matched_activity_id": w.matched_activity_id,
    }


def _parse_dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _constraint_messages(db: Session, athlete_id: int, scheduled_at: datetime, duration_s: float | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    day = scheduled_at.date()
    constraints = list(db.scalars(select(PlanningConstraint).where(
        PlanningConstraint.athlete_id == athlete_id,
        PlanningConstraint.enabled.is_(True),
        PlanningConstraint.start_date <= day,
    )))
    for c in constraints:
        if c.end_date and day > c.end_date:
            continue
        if c.weekdays and day.weekday() not in c.weekdays:
            continue
        value = c.value or {}
        target = errors if value.get("hard", True) else warnings
        if c.constraint_type == "unavailable":
            target.append(f"{day.isoformat()} is unavailable (constraint {c.id}).")
        if c.constraint_type in {"availability", "max_daily_hours"} and value.get("max_hours") is not None:
            max_h = float(value["max_hours"])
            if float(duration_s or 0) > max_h * 3600:
                target.append(f"Workout on {day.isoformat()} exceeds {max_h:g}h availability (constraint {c.id}).")
    return errors, warnings


def _normalized_command(raw: dict) -> dict:
    command = PlanCommand.model_validate(raw)
    result = command.model_dump(exclude_none=True)
    if command.scheduled_at:
        result["scheduled_at"] = command.scheduled_at.isoformat()
    return result


def validate_commands(db: Session, athlete_id: int, commands: list[dict], today: date | None = None) -> dict:
    today = today or date.today()
    errors: list[str] = []
    soft: list[str] = []
    normalized: list[dict] = []
    try:
        normalized = [_normalized_command(c) for c in commands]
    except Exception as exc:
        return {"valid": False, "errors": [f"Invalid command schema: {exc}"], "warnings": [], "commands": []}

    start = today - timedelta(days=7)
    end = today + timedelta(days=42)
    workouts = list(db.scalars(select(PlannedWorkout).where(
        PlannedWorkout.athlete_id == athlete_id,
        PlannedWorkout.scheduled_at >= datetime.combine(start, datetime.min.time()),
        PlannedWorkout.scheduled_at <= datetime.combine(end, datetime.max.time()),
    ).order_by(PlannedWorkout.scheduled_at)))
    current = {w.id: workout_dict(w) for w in workouts}
    simulated = deepcopy(current)

    for index, c in enumerate(normalized):
        action = c["action"]
        if action == "create_workout":
            scheduled = _parse_dt(c.get("scheduled_at"))
            if scheduled is None:
                errors.append(f"Command {index}: missing scheduled_at")
                continue
            if scheduled.date() < today:
                errors.append(f"Command {index}: AI cannot create workouts in the past")
                continue
            duration = float(c.get("duration_s") or 0)
            hard, warn = _constraint_messages(db, athlete_id, scheduled, duration)
            errors.extend(hard); soft.extend(warn)
            estimate = estimate_planned_load(duration, c.get("steps"), c.get("intensity"))
            fake_id = -(index + 1)
            simulated[fake_id] = {
                "id": fake_id, "scheduled_at": scheduled.isoformat(), "sport": c["sport"], "name": c["name"],
                "duration_s": duration, "distance_m": c.get("distance_m"), "elevation_m": c.get("elevation_m"),
                "projected_load": estimate.load, "steps": c.get("steps") or [], "intensity": c.get("intensity") or infer_intensity(c.get("steps")),
                "locked": False, "objective_id": c.get("objective_id"), "block_id": c.get("block_id"), "matched_activity_id": None,
            }
            continue

        workout_id = c.get("workout_id")
        item = simulated.get(workout_id)
        db_item = db.get(PlannedWorkout, workout_id) if workout_id else None
        if not db_item or db_item.athlete_id != athlete_id or item is None:
            errors.append(f"Command {index}: workout {workout_id} was not found for this athlete")
            continue
        if db_item.locked:
            errors.append(f"Command {index}: workout {workout_id} is locked")
            continue
        if db_item.scheduled_at.date() < today:
            errors.append(f"Command {index}: AI cannot modify past workout {workout_id}")
            continue
        if action == "delete_workout":
            simulated.pop(workout_id, None)
            continue

        if action in {"move_workout", "update_workout"}:
            if c.get("scheduled_at"):
                item["scheduled_at"] = _parse_dt(c["scheduled_at"]).isoformat()
            for field in ("sport", "name", "duration_s", "distance_m", "elevation_m", "steps", "intensity"):
                if field in c:
                    item[field] = c[field]
            scheduled = _parse_dt(item["scheduled_at"])
            if scheduled.date() < today:
                errors.append(f"Command {index}: resulting workout date is in the past")
            hard, warn = _constraint_messages(db, athlete_id, scheduled, item.get("duration_s"))
            errors.extend(hard); soft.extend(warn)
            if any(k in c for k in ("duration_s", "steps", "intensity")):
                item["projected_load"] = estimate_planned_load(item.get("duration_s"), item.get("steps"), item.get("intensity")).load

    # Detect multiple planned hard sessions on the same day as a simple schedule warning.
    by_day: dict[str, list[dict]] = {}
    for w in simulated.values():
        d = str(w["scheduled_at"])[:10]
        by_day.setdefault(d, []).append(w)
    for d, items in by_day.items():
        hard = [w for w in items if w.get("intensity") in {"threshold", "vo2", "anaerobic", "race"}]
        if len(hard) > 1:
            soft.append(f"{d} contains {len(hard)} hard planned sessions.")

    calc_start = today - timedelta(days=90)
    actual_rows = db.execute(select(func.date(Activity.start_time), func.sum(ActivityMetrics.training_load)).join(ActivityMetrics).where(
        Activity.athlete_id == athlete_id,
        Activity.start_time >= datetime.combine(calc_start, datetime.min.time()),
        Activity.start_time <= datetime.combine(today, datetime.max.time()),
    ).group_by(func.date(Activity.start_time))).all()
    actual = {date.fromisoformat(str(d)): float(load or 0) for d, load in actual_rows}

    def projection_for(items: list[dict]):
        planned_daily: dict[date, float] = {}
        key_sessions = []
        for w in items:
            scheduled = _parse_dt(w["scheduled_at"])
            d = scheduled.date()
            if d > end:
                continue
            planned_daily[d] = planned_daily.get(d, 0) + float(w.get("projected_load") or 0)
            key_sessions.append((scheduled, w.get("intensity") or "endurance", float(w.get("projected_load") or 0)))
        rows = project_load_series(actual, planned_daily, calc_start, end)
        visible = [r for r in rows if r["date"] >= today.isoformat()]
        return visible, projection_warnings(visible, key_sessions)

    before_rows, before_warnings = projection_for(list(current.values()))
    after_rows, after_warnings = projection_for(list(simulated.values()))
    before_keys = {(w.get("code"), w.get("week"), w.get("date")) for w in before_warnings}
    new_projection_warnings = [w for w in after_warnings if (w.get("code"), w.get("week"), w.get("date")) not in before_keys]
    warning_objects = [{"code": "constraint_warning", "severity": "warning", "message": m} for m in soft] + new_projection_warnings

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warning_objects,
        "commands": normalized,
        "before_weeks": weekly_totals(before_rows),
        "after_weeks": weekly_totals(after_rows),
    }


def apply_commands(db: Session, athlete_id: int, commands: list[dict], proposal_reason: str | None = None) -> list[dict]:
    validation = validate_commands(db, athlete_id, commands)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["errors"]))
    applied: list[dict] = []
    normalized = validation["commands"]
    for c in normalized:
        action = c["action"]
        reason = c.get("reason") or proposal_reason
        if action == "create_workout":
            duration = float(c.get("duration_s") or 0)
            steps = c.get("steps") or []
            intensity = c.get("intensity")
            estimate = estimate_planned_load(duration, steps, intensity)
            if intensity and not steps:
                steps = [{"type": "main", "intensity": intensity, "duration_s": duration}]
            w = PlannedWorkout(
                athlete_id=athlete_id,
                scheduled_at=_parse_dt(c["scheduled_at"]),
                sport=c["sport"], name=c["name"], duration_s=duration,
                distance_m=c.get("distance_m"), elevation_m=c.get("elevation_m"),
                projected_load=estimate.load, steps=steps, locked=False,
                objective_id=c.get("objective_id"), block_id=c.get("block_id"),
            )
            db.add(w); db.flush()
            after = workout_dict(w)
            db.add(PlanChangeAudit(athlete_id=athlete_id, entity_type="planned_workout", entity_id=w.id, action="create", before_state=None, after_state=after, reason=reason, initiated_by="ai"))
            applied.append(after)
            continue

        w = db.get(PlannedWorkout, c["workout_id"])
        if not w or w.athlete_id != athlete_id:
            raise ValueError(f"Workout {c['workout_id']} disappeared before proposal application")
        before = workout_dict(w)
        if action == "delete_workout":
            db.add(PlanChangeAudit(athlete_id=athlete_id, entity_type="planned_workout", entity_id=w.id, action="delete", before_state=before, after_state=None, reason=reason, initiated_by="ai"))
            db.delete(w)
            applied.append({"id": before["id"], "deleted": True})
            continue
        if c.get("scheduled_at"):
            w.scheduled_at = _parse_dt(c["scheduled_at"])
        for field in ("sport", "name", "duration_s", "distance_m", "elevation_m", "steps", "objective_id", "block_id"):
            if field in c:
                setattr(w, field, c[field])
        if c.get("intensity") is not None:
            if not c.get("steps"):
                w.steps = [{"type": "main", "intensity": c["intensity"], "duration_s": w.duration_s}]
        if any(k in c for k in ("duration_s", "steps", "intensity")):
            w.projected_load = estimate_planned_load(w.duration_s, w.steps, c.get("intensity")).load
        db.flush()
        after = workout_dict(w)
        db.add(PlanChangeAudit(athlete_id=athlete_id, entity_type="planned_workout", entity_id=w.id, action="update" if action == "update_workout" else "move", before_state=before, after_state=after, reason=reason, initiated_by="ai"))
        applied.append(after)
    db.commit()
    return applied
