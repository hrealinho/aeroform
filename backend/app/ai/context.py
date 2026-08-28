from __future__ import annotations
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.domain.models import (
    Activity, ActivityMetrics, Athlete, AthleteThreshold, Objective, PlannedWorkout,
    TrainingBlock, PlanningConstraint, AthleteCoachProfile,
)
from app.metrics.fitness import ewma_series
from app.planning.workouts import infer_intensity


def _dt(day: date, end: bool = False) -> datetime:
    return datetime.combine(day, datetime.max.time() if end else datetime.min.time())


def _round(v, digits=1):
    return round(float(v or 0), digits)


def _current_profile(db: Session, athlete_id: int) -> AthleteCoachProfile | None:
    return db.scalar(select(AthleteCoachProfile).where(AthleteCoachProfile.athlete_id == athlete_id))


def build_athlete_context(db: Session, athlete: Athlete, today: date | None = None) -> dict:
    """Build a compact, structured athlete context from derived data only.

    Raw FIT/GPX streams are deliberately excluded. The context is small enough for
    an LLM but rich enough for deterministic planning and analysis.
    """
    today = today or date.today()
    history_start = today - timedelta(days=365)
    recent_start = today - timedelta(days=90)
    rows = db.execute(
        select(Activity, ActivityMetrics)
        .join(ActivityMetrics)
        .where(
            Activity.athlete_id == athlete.id,
            Activity.start_time >= _dt(history_start),
            Activity.start_time <= _dt(today, True),
        )
        .order_by(Activity.start_time)
    ).all()

    daily_load: dict[date, float] = defaultdict(float)
    weekly: dict[date, dict] = {}
    sport_28: dict[str, dict] = defaultdict(lambda: {"hours": 0.0, "distance_km": 0.0, "elevation_gain_m": 0.0, "elevation_loss_m": 0.0, "load": 0.0, "metabolic_load": 0.0, "mechanical_load": 0.0, "descent_load": 0.0, "sessions": 0})
    longest_90: dict[str, dict] = {}
    hardest_recent: list[dict] = []
    recent_activities: list[dict] = []

    for activity, metrics in rows:
        day = activity.start_time.date()
        load = float(metrics.training_load or 0)
        daily_load[day] += load
        monday = day - timedelta(days=day.weekday())
        b = weekly.setdefault(monday, {"week": monday.isoformat(), "load": 0.0, "metabolic_load": 0.0, "mechanical_load": 0.0, "descent_load": 0.0, "hours": 0.0, "distance_km": 0.0, "elevation_gain_m": 0.0, "elevation_loss_m": 0.0, "sessions": 0})
        details = metrics.details if isinstance(metrics.details, dict) else {}
        terrain = details.get("terrain") if isinstance(details.get("terrain"), dict) else {}
        b["load"] += load
        b["metabolic_load"] += float(details.get("metabolic_load") or load)
        b["mechanical_load"] += float(metrics.mechanical_load or 0)
        b["descent_load"] += float(terrain.get("descent_load") or 0)
        b["hours"] += float(activity.duration_s or 0) / 3600
        b["distance_km"] += float(activity.distance_m or 0) / 1000
        b["elevation_gain_m"] += float(activity.elevation_gain_m or 0)
        b["elevation_loss_m"] += float(activity.elevation_loss_m or 0)
        b["sessions"] += 1
        if day >= today - timedelta(days=28):
            s = sport_28[activity.sport]
            s["hours"] += float(activity.duration_s or 0) / 3600
            s["distance_km"] += float(activity.distance_m or 0) / 1000
            s["elevation_gain_m"] += float(activity.elevation_gain_m or 0)
            s["elevation_loss_m"] += float(activity.elevation_loss_m or 0)
            s["load"] += load
            s["metabolic_load"] += float(details.get("metabolic_load") or load)
            s["mechanical_load"] += float(metrics.mechanical_load or 0)
            s["descent_load"] += float(terrain.get("descent_load") or 0)
            s["sessions"] += 1
        if day >= recent_start:
            current = longest_90.get(activity.sport)
            if not current or float(activity.duration_s or 0) > current["duration_s"]:
                longest_90[activity.sport] = {
                    "activity_id": activity.id,
                    "date": day.isoformat(),
                    "name": activity.name,
                    "duration_s": _round(activity.duration_s, 0),
                    "distance_km": _round((activity.distance_m or 0) / 1000),
                    "elevation_gain_m": _round(activity.elevation_gain_m, 0),
                    "elevation_loss_m": _round(activity.elevation_loss_m, 0),
                    "load": _round(load),
                    "mechanical_load": _round(metrics.mechanical_load),
                    "descent_load": _round(terrain.get("descent_load")),
                }
            item = {"activity_id": activity.id, "date": day.isoformat(), "start_time": activity.start_time.isoformat(), "sport": activity.sport, "name": activity.name, "load": _round(load), "metabolic_load": _round(details.get("metabolic_load") or load), "mechanical_load": _round(metrics.mechanical_load), "descent_load": _round(terrain.get("descent_load")), "duration_h": _round((activity.duration_s or 0) / 3600), "distance_km": _round((activity.distance_m or 0) / 1000), "elevation_gain_m": _round(activity.elevation_gain_m, 0), "elevation_loss_m": _round(activity.elevation_loss_m, 0)}
            hardest_recent.append(item)
            if day >= today - timedelta(days=14):
                recent_activities.append(item)

    fitness_rows = ewma_series(daily_load, history_start, today)
    state = fitness_rows[-1] if fitness_rows else {"fitness": 0, "fatigue": 0, "form": 0, "load": 0}
    week_rows = [{**v, "load": _round(v["load"]), "metabolic_load": _round(v["metabolic_load"]), "mechanical_load": _round(v["mechanical_load"]), "descent_load": _round(v["descent_load"]), "hours": _round(v["hours"]), "distance_km": _round(v["distance_km"]), "elevation_gain_m": _round(v["elevation_gain_m"], 0), "elevation_loss_m": _round(v["elevation_loss_m"], 0), "elevation_m": _round(v["elevation_gain_m"], 0)} for _, v in sorted(weekly.items())]
    completed_weeks = [w for w in week_rows if date.fromisoformat(w["week"]) < today - timedelta(days=today.weekday())]
    recent_completed = completed_weeks[-8:]
    typical_weekly_load = median([w["load"] for w in recent_completed]) if recent_completed else 0.0
    typical_weekly_hours = median([w["hours"] for w in recent_completed]) if recent_completed else 0.0

    load_7 = sum(v for d, v in daily_load.items() if d >= today - timedelta(days=6))
    load_28 = sum(v for d, v in daily_load.items() if d >= today - timedelta(days=27))
    recent_four = week_rows[-4:]
    prior_four = week_rows[-8:-4]

    thresholds = list(db.scalars(select(AthleteThreshold).where(
        AthleteThreshold.athlete_id == athlete.id,
        AthleteThreshold.valid_from <= today,
    ).order_by(AthleteThreshold.valid_from.desc())))
    current_thresholds = {}
    seen = set()
    for t in thresholds:
        key = f"{t.sport}:{t.metric}"
        if key in seen or (t.valid_until and t.valid_until < today):
            continue
        seen.add(key)
        current_thresholds[key] = {"value": t.value, "source": t.source, "confidence": t.confidence, "valid_from": t.valid_from.isoformat()}

    objectives = list(db.scalars(select(Objective).where(
        Objective.athlete_id == athlete.id,
        Objective.event_date >= today,
    ).order_by(Objective.event_date).limit(10)))
    objectives_data = [{
        "id": o.id, "name": o.name, "date": o.event_date.isoformat(), "days_away": (o.event_date - today).days,
        "sport": o.sport, "subtype": o.subtype, "priority": o.priority,
        "distance_km": _round((o.distance_m or 0) / 1000), "elevation_m": _round(o.elevation_m, 0),
        "target": o.target or {},
    } for o in objectives]

    current_block = db.scalar(select(TrainingBlock).where(
        TrainingBlock.athlete_id == athlete.id,
        TrainingBlock.start_date <= today,
        TrainingBlock.end_date >= today,
    ).order_by(TrainingBlock.start_date.desc()))
    block_data = None if current_block is None else {
        "id": current_block.id, "name": current_block.name, "type": current_block.block_type,
        "start_date": current_block.start_date.isoformat(), "end_date": current_block.end_date.isoformat(), "targets": current_block.targets or {},
    }

    constraints = list(db.scalars(select(PlanningConstraint).where(
        PlanningConstraint.athlete_id == athlete.id,
        PlanningConstraint.enabled.is_(True),
        PlanningConstraint.start_date <= today + timedelta(days=60),
    ).order_by(PlanningConstraint.start_date)))
    constraints_data = [{"id": c.id, "type": c.constraint_type, "start_date": c.start_date.isoformat(), "end_date": c.end_date.isoformat() if c.end_date else None, "weekdays": c.weekdays or [], "value": c.value or {}} for c in constraints if not c.end_date or c.end_date >= today]

    profile = _current_profile(db, athlete.id)
    profile_data = {
        "available_hours_per_week": profile.available_hours_per_week if profile else None,
        "preferred_long_day": profile.preferred_long_day if profile else 5,
        "preferred_rest_day": profile.preferred_rest_day if profile else 0,
        "doubles_allowed": profile.doubles_allowed if profile else False,
        "preferences": profile.preferences if profile else {},
    }

    plan_start = today - timedelta(days=28)
    plan_end = today + timedelta(days=21)
    planned = list(db.scalars(select(PlannedWorkout).where(
        PlannedWorkout.athlete_id == athlete.id,
        PlannedWorkout.scheduled_at >= _dt(plan_start),
        PlannedWorkout.scheduled_at <= _dt(plan_end, True),
    ).order_by(PlannedWorkout.scheduled_at)))
    planned_data = [{
        "id": w.id, "date": w.scheduled_at.date().isoformat(), "scheduled_at": w.scheduled_at.isoformat(), "sport": w.sport,
        "name": w.name, "duration_s": w.duration_s, "projected_load": _round(w.projected_load), "intensity": infer_intensity(w.steps),
        "locked": w.locked, "matched_activity_id": w.matched_activity_id,
    } for w in planned]
    past_plan = [w for w in planned if w.scheduled_at.date() < today and w.scheduled_at.date() >= today - timedelta(days=28)]
    matched = [w for w in past_plan if w.matched_activity_id]
    adherence = round(len(matched) / len(past_plan) * 100, 1) if past_plan else None

    min_date, max_date, activity_count = db.execute(select(func.min(Activity.start_time), func.max(Activity.start_time), func.count(Activity.id)).where(Activity.athlete_id == athlete.id)).one()

    for data in sport_28.values():
        for key in ("hours", "distance_km", "elevation_gain_m", "elevation_loss_m", "load", "metabolic_load", "mechanical_load", "descent_load"):
            data[key] = _round(data[key], 0 if key in {"elevation_gain_m", "elevation_loss_m"} else 1)
        data["elevation_m"] = data["elevation_gain_m"]
    hardest_recent = sorted(hardest_recent, key=lambda x: x["load"], reverse=True)[:5]

    return {
        "as_of": today.isoformat(),
        "athlete": {"id": athlete.id, "display_name": athlete.display_name, "timezone": athlete.timezone},
        "history": {"activity_count": activity_count or 0, "first_activity": min_date.date().isoformat() if min_date else None, "last_activity": max_date.date().isoformat() if max_date else None},
        "state": {"fitness": state.get("fitness", 0), "fatigue": state.get("fatigue", 0), "form": state.get("form", 0), "load_7d": _round(load_7), "load_28d": _round(load_28), "typical_weekly_load_8w": _round(typical_weekly_load), "typical_weekly_hours_8w": _round(typical_weekly_hours)},
        "recent_weeks": week_rows[-12:],
        "recent_four_weeks": recent_four,
        "prior_four_weeks": prior_four,
        "sports_28d": dict(sport_28),
        "longest_90d": longest_90,
        "hardest_recent": hardest_recent,
        "recent_activities": sorted(recent_activities, key=lambda x: x["start_time"]),
        "thresholds": current_thresholds,
        "objectives": objectives_data,
        "current_block": block_data,
        "constraints": constraints_data,
        "profile": profile_data,
        "planned": planned_data,
        "adherence_28d_pct": adherence,
    }
