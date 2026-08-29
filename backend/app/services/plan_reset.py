"""Removing plans and target races.

Deleting planned work needs more care than deleting an activity, because a plan is a
graph: an objective owns blocks, blocks own workouts, and the race itself is a locked
workout created by the planner. Removing one piece and leaving the rest produces exactly
the state that is hardest to reason about - orphaned blocks with no race, or a locked race
workout for an objective that no longer exists and which no endpoint would delete.

Two rules:
  - Completed work is never touched. Past workouts are training history, matched or not.
  - Everything removed is audited, because a plan reset is destructive and the athlete
    should be able to see what went.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import day_start, to_utc
from app.domain.models import Objective, PlanChangeAudit, PlannedWorkout, TrainingBlock

@dataclass
class ResetResult:
    workouts_deleted: int = 0
    blocks_deleted: int = 0
    locked_skipped: int = 0
    matched_skipped: int = 0
    objectives_deleted: int = 0
    details: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "workouts_deleted": self.workouts_deleted,
            "blocks_deleted": self.blocks_deleted,
            "locked_skipped": self.locked_skipped,
            "matched_skipped": self.matched_skipped,
            "objectives_deleted": self.objectives_deleted,
        }


def reset_plan(
    db: Session,
    athlete_id: int,
    scope: str = "future",
    objective_id: int | None = None,
    include_locked: bool = False,
    today: date | None = None,
) -> ResetResult:
    """Delete planned workouts and training blocks.

    ``scope="future"`` leaves everything before today alone, which is almost always what
    is wanted: a plan reset should not erase the record of what was prescribed for work
    already done. ``include_locked`` is required to remove a locked workout - including
    the race - so a reset cannot silently delete something deliberately pinned.
    """
    today = today or date.today()
    result = ResetResult()

    workout_query = select(PlannedWorkout).where(PlannedWorkout.athlete_id == athlete_id)
    if scope == "future":
        workout_query = workout_query.where(PlannedWorkout.scheduled_at >= day_start(today))
    if objective_id is not None:
        workout_query = workout_query.where(PlannedWorkout.objective_id == objective_id)

    for workout in db.scalars(workout_query):
        if workout.locked and not include_locked:
            result.locked_skipped += 1
            continue
        # A matched workout is paired with a completed activity; deleting it loses the
        # only record that the session was prescribed.
        if workout.matched_activity_id is not None and scope == "future":
            result.matched_skipped += 1
            continue
        result.details.append({
            "id": workout.id, "name": workout.name,
            "scheduled_at": to_utc(workout.scheduled_at).isoformat(),
            "locked": bool(workout.locked),
        })
        db.delete(workout)
        result.workouts_deleted += 1

    block_query = select(TrainingBlock).where(TrainingBlock.athlete_id == athlete_id)
    if objective_id is not None:
        block_query = block_query.where(TrainingBlock.objective_id == objective_id)
    if scope == "future":
        block_query = block_query.where(TrainingBlock.end_date >= today)

    for block in db.scalars(block_query):
        db.delete(block)
        result.blocks_deleted += 1

    if result.workouts_deleted or result.blocks_deleted:
        db.add(PlanChangeAudit(
            athlete_id=athlete_id, entity_type="plan_reset", entity_id=objective_id,
            action="delete", before_state={"removed": result.details[:100], **result.as_dict()},
            after_state=None, initiated_by="user",
            reason=f"Plan reset (scope={scope}"
                   + (f", objective={objective_id}" if objective_id else "")
                   + (", including locked" if include_locked else "") + ")",
        ))
    db.commit()
    return result


def delete_objective_with_plan(db: Session, athlete_id: int, objective_id: int,
                               with_plan: bool = False) -> ResetResult | None:
    """Delete a target race, optionally taking its plan with it.

    Without ``with_plan`` the blocks and workouts are detached rather than deleted, so a
    plan can outlive the race it was built for. With it, the race workout goes too - which
    is the only way to remove a locked race, since the planner creates it locked.
    """
    objective = db.get(Objective, objective_id)
    if not objective or objective.athlete_id != athlete_id:
        return None

    result = ResetResult()
    if with_plan:
        # include_locked, or the race workout the planner created survives its own race.
        result = reset_plan(db, athlete_id, scope="all", objective_id=objective_id, include_locked=True)

    # Whatever remains is detached: Postgres would reject the delete on a live foreign key,
    # and SQLite would leave a dangling reference.
    from sqlalchemy import update

    db.execute(update(PlannedWorkout).where(
        PlannedWorkout.athlete_id == athlete_id, PlannedWorkout.objective_id == objective_id,
    ).values(objective_id=None))
    db.execute(update(TrainingBlock).where(
        TrainingBlock.athlete_id == athlete_id, TrainingBlock.objective_id == objective_id,
    ).values(objective_id=None))

    db.add(PlanChangeAudit(
        athlete_id=athlete_id, entity_type="objective", entity_id=objective_id, action="delete",
        before_state={"name": objective.name, "event_date": objective.event_date.isoformat(),
                      "priority": objective.priority, "sport": objective.sport},
        after_state=None, initiated_by="user",
        reason="Target race deleted" + (" with its plan" if with_plan else ""),
    ))
    db.delete(objective)
    result.objectives_deleted = 1
    db.commit()
    return result
