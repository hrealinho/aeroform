"""Periodisation: turn an objective into blocks, and blocks into weekly targets.

This is the layer that was missing. Objectives existed, training blocks existed, and
individual workouts existed, but nothing connected them: blocks were hand-picked with
arbitrary dates, their targets were read by almost nothing, and the planner generated
one week at a time with no knowledge of the arc toward the race.

The model is classic linear periodisation, chosen because it is the best understood
and because it matches the block types the data model already carries. Everything is
deterministic and every number is derived from the athlete's own recent load, so a
plan can be explained rather than asserted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

# Weekly load may rise this much week to week. Below the projection engine's 15%
# spike warning on purpose: a plan should not generate its own warnings.
MAX_WEEKLY_RAMP = 1.07
# Ceiling on how far a plan may take an athlete above their established load.
MAX_PEAK_MULTIPLE = 1.45
# Every Nth week is easier. 3:1 is the conventional loading pattern.
RECOVERY_EVERY = 4
RECOVERY_FACTOR = 0.62

# Fraction of peak weekly load each phase targets.
PHASE_INTENSITY = {
    "foundation": 0.80,
    "base": 0.90,
    "build": 1.00,
    "specific": 1.00,
    "peak": 0.85,
    "taper": 0.55,
    "recovery": RECOVERY_FACTOR,
}


@dataclass
class PlannedWeek:
    index: int
    start: date
    phase: str
    target_load: float
    target_hours: float
    is_recovery: bool
    note: str

    def as_dict(self) -> dict:
        return {
            "index": self.index, "week_start": self.start.isoformat(), "phase": self.phase,
            "target_load": round(self.target_load, 1), "target_hours": round(self.target_hours, 1),
            "is_recovery": self.is_recovery, "note": self.note,
        }


@dataclass
class PlannedBlock:
    name: str
    block_type: str
    start: date
    end: date
    weeks: list[PlannedWeek] = field(default_factory=list)

    def as_dict(self) -> dict:
        loads = [w.target_load for w in self.weeks]
        return {
            "name": self.name, "block_type": self.block_type,
            "start_date": self.start.isoformat(), "end_date": self.end.isoformat(),
            "weeks": len(self.weeks),
            "targets": {
                # weekly_load is the mean across the block; the planner reads it, and the
                # per-week targets below carry the actual progression.
                "weekly_load": round(sum(loads) / len(loads), 1) if loads else 0.0,
                "weekly_hours": round(sum(w.target_hours for w in self.weeks) / len(self.weeks), 1) if self.weeks else 0.0,
                "peak_weekly_load": round(max(loads), 1) if loads else 0.0,
                "week_targets": [w.as_dict() for w in self.weeks],
            },
        }


def _phase_sequence(total_weeks: int, long_objective: bool) -> list[str]:
    """Assign a phase to every week, working backwards from the race.

    Short horizons collapse gracefully: with three weeks left there is no point
    prescribing a base phase, so the plan becomes peak plus taper.
    """
    if total_weeks <= 0:
        return []
    taper = 2 if long_objective else 1
    if total_weeks <= 2:
        return ["taper"] * total_weeks
    if total_weeks <= 4:
        return ["peak"] * (total_weeks - taper) + ["taper"] * taper

    remaining = total_weeks - taper
    peak = 2
    remaining -= peak
    specific = min(4, max(2, remaining // 3))
    remaining -= specific
    build = min(6, max(2, remaining // 2))
    remaining -= build
    base = max(0, remaining)

    return (["base"] * base) + (["build"] * build) + (["specific"] * specific) \
        + (["peak"] * peak) + (["taper"] * taper)


def periodise(
    start: date,
    objective_date: date,
    typical_weekly_load: float,
    typical_weekly_hours: float,
    long_objective: bool = False,
    objective_name: str = "objective",
) -> list[PlannedBlock]:
    """Build the block structure and weekly targets between start and the objective."""
    start = start - timedelta(days=start.weekday())
    total_weeks = max(0, (objective_date - start).days // 7)
    phases = _phase_sequence(total_weeks, long_objective)
    if not phases:
        return []

    base_load = max(float(typical_weekly_load or 0), 1.0)
    base_hours = max(float(typical_weekly_hours or 0), 0.5)
    load_per_hour = base_load / base_hours

    # Peak is reached by ramping, but never beyond what the athlete has established.
    ramp_weeks = sum(1 for p in phases if p in {"base", "build", "specific"})
    peak_load = min(base_load * (MAX_WEEKLY_RAMP ** max(ramp_weeks, 1)), base_load * MAX_PEAK_MULTIPLE)

    weeks: list[PlannedWeek] = []
    for index, phase in enumerate(phases):
        # Recovery weeks interrupt loading phases only; a taper is already easy.
        is_recovery = phase in {"base", "build", "specific"} and (index + 1) % RECOVERY_EVERY == 0
        factor = RECOVERY_FACTOR if is_recovery else PHASE_INTENSITY.get(phase, 1.0)

        if phase in {"base", "build", "specific"}:
            # Progress toward peak across the loading phases.
            progress = (index + 1) / max(ramp_weeks, 1)
            ceiling = base_load + (peak_load - base_load) * min(progress, 1.0)
        else:
            ceiling = peak_load

        target_load = ceiling * factor
        note = "recovery week" if is_recovery else f"{phase} week"
        if phase == "taper":
            note = f"taper into {objective_name}"
        weeks.append(PlannedWeek(
            index=index, start=start + timedelta(weeks=index), phase=phase,
            target_load=target_load, target_hours=target_load / load_per_hour,
            is_recovery=is_recovery, note=note,
        ))

    # Collapse consecutive same-phase weeks into blocks.
    blocks: list[PlannedBlock] = []
    for week in weeks:
        if blocks and blocks[-1].block_type == week.phase:
            blocks[-1].weeks.append(week)
            blocks[-1].end = week.start + timedelta(days=6)
        else:
            blocks.append(PlannedBlock(
                name=f"{week.phase.title()} - {objective_name}", block_type=week.phase,
                start=week.start, end=week.start + timedelta(days=6), weeks=[week],
            ))
    return blocks


def week_target(blocks: list[dict], week_start: date) -> dict | None:
    """Find the stored per-week target covering a week, so the week planner can use it."""
    iso = week_start.isoformat()
    for block in blocks:
        for target in ((block.get("targets") or {}).get("week_targets") or []):
            if target.get("week_start") == iso:
                return {**target, "block_type": block.get("block_type"), "block_name": block.get("name")}
    return None
