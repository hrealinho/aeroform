from __future__ import annotations
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActivityMetrics, Objective, PlannedWorkout, TrainingBlock
from app.planning.workouts import infer_intensity


def explain_workout(db: Session, athlete_id: int, workout: PlannedWorkout) -> dict:
    intensity = infer_intensity(workout.steps)
    objective = db.get(Objective, workout.objective_id) if workout.objective_id else None
    block = db.get(TrainingBlock, workout.block_id) if workout.block_id else None
    start = workout.scheduled_at - timedelta(days=70)
    comparable = db.execute(select(Activity, ActivityMetrics).outerjoin(ActivityMetrics).where(
        Activity.athlete_id == athlete_id,
        Activity.sport == workout.sport,
        Activity.start_time >= start,
        Activity.start_time < workout.scheduled_at,
    ).order_by(Activity.start_time.desc()).limit(20)).all()
    comparable_sorted = sorted(comparable, key=lambda row: float(row[0].duration_s or 0), reverse=True)[:3]
    evidence = [{
        "activity_id": a.id, "date": a.start_time.date().isoformat(), "name": a.name,
        "duration_h": round(float(a.duration_s or 0) / 3600, 1), "load": round(float(m.training_load or 0), 1) if m else None,
    } for a, m in comparable_sorted]

    reasons = []
    if objective:
        reasons.append(f"Supports {objective.priority}-priority objective '{objective.name}' on {objective.event_date.isoformat()}.")
    elif block:
        reasons.append(f"Supports the {block.block_type} focus of the '{block.name}' block.")
    else:
        reasons.append("Supports the current training week; no specific objective/block link is stored yet.")
    reasons.append(f"Prescription is {intensity} for about {round(float(workout.duration_s or 0) / 60)} minutes, estimated at {round(float(workout.projected_load or 0))} planned load.")
    if evidence:
        longest = evidence[0]
        reasons.append(f"Recent same-sport context includes a {longest['duration_h']:.1f} h session on {longest['date']}; this is used as progression context, not proof of readiness.")
    if workout.locked:
        reasons.append("The workout is locked, so the AI planner may explain it but cannot modify it.")

    return {
        "workout_id": workout.id,
        "title": f"Why {workout.name}?",
        "reasons": reasons,
        "evidence": evidence,
        "deterministic_facts": True,
        "judgement_note": "Progression and scheduling rationale are planning judgements. They should be reviewed if recovery, illness, injury, travel, or availability changes.",
    }
