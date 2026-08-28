from __future__ import annotations
from datetime import date, datetime, time, timedelta

from app.planning.workouts import estimate_planned_load

HARD = {"threshold", "vo2", "anaerobic", "race"}


def _primary_objective(context: dict, objective_id: int | None = None) -> dict | None:
    objectives = context.get("objectives") or []
    if objective_id is not None:
        return next((o for o in objectives if o["id"] == objective_id), None)
    a = [o for o in objectives if o.get("priority") == "A"]
    return (a or objectives or [None])[0]


def _primary_sport(context: dict, objective: dict | None) -> str:
    if objective and objective.get("sport"):
        return objective["sport"]
    sports = context.get("sports_28d") or {}
    if sports:
        return max(sports, key=lambda s: float((sports[s] or {}).get("load") or 0))
    return "running"


def _constraint_for_day(context: dict, day: date, duration_h: float) -> tuple[bool, str | None]:
    for c in context.get("constraints") or []:
        start = date.fromisoformat(c["start_date"])
        end = date.fromisoformat(c["end_date"]) if c.get("end_date") else None
        if day < start or (end and day > end):
            continue
        weekdays = c.get("weekdays") or []
        if weekdays and day.weekday() not in weekdays:
            continue
        value = c.get("value") or {}
        if not value.get("hard", True):
            continue
        if c["type"] == "unavailable":
            return False, "unavailable"
        if c["type"] in {"max_daily_hours", "availability"}:
            max_h = value.get("max_hours")
            if max_h is not None and duration_h > float(max_h):
                return False, f"max {max_h}h"
    return True, None


def _steps(intensity: str, duration_s: float) -> list[dict]:
    if intensity == "threshold" and duration_s >= 3000:
        # 15m warm-up + 4x(8m/2m) + remaining cooldown.
        used = 15 * 60 + 4 * (8 * 60 + 2 * 60)
        cooldown = max(duration_s - used, 5 * 60)
        return [
            {"type": "warmup", "intensity": "easy", "duration_s": 15 * 60},
            {"type": "repeat", "repeat": 4, "steps": [{"type": "work", "intensity": "threshold", "duration_s": 8 * 60}, {"type": "recovery", "intensity": "easy", "duration_s": 2 * 60}]},
            {"type": "cooldown", "intensity": "easy", "duration_s": cooldown},
        ]
    if intensity == "vo2" and duration_s >= 2700:
        used = 15 * 60 + 5 * (3 * 60 + 3 * 60)
        cooldown = max(duration_s - used, 5 * 60)
        return [
            {"type": "warmup", "intensity": "easy", "duration_s": 15 * 60},
            {"type": "repeat", "repeat": 5, "steps": [{"type": "work", "intensity": "vo2", "duration_s": 3 * 60}, {"type": "recovery", "intensity": "easy", "duration_s": 3 * 60}]},
            {"type": "cooldown", "intensity": "easy", "duration_s": cooldown},
        ]
    return [{"type": "main", "intensity": intensity, "duration_s": duration_s}]


def _template(sport: str, long_day: int) -> list[dict]:
    if sport == "cycling":
        return [
            {"weekday": 1, "name": "Threshold bike", "intensity": "threshold", "minutes": 75, "key": True},
            {"weekday": 3, "name": "Endurance ride", "intensity": "endurance", "minutes": 90},
            {"weekday": long_day, "name": "Long endurance ride", "intensity": "endurance", "minutes": 210, "key": True},
            {"weekday": (long_day + 1) % 7, "name": "Recovery spin", "intensity": "recovery", "minutes": 60},
        ]
    if sport in {"trail_running", "hiking", "mountaineering"}:
        training_sport = "trail_running" if sport == "trail_running" else sport
        return [
            {"weekday": 1, "name": "Uphill threshold", "intensity": "threshold", "minutes": 60, "key": True, "sport": training_sport},
            {"weekday": 2, "name": "Easy aerobic", "intensity": "easy", "minutes": 50, "sport": "running" if sport == "trail_running" else training_sport},
            {"weekday": 4, "name": "Easy aerobic", "intensity": "easy", "minutes": 45, "sport": "running" if sport == "trail_running" else training_sport},
            {"weekday": long_day, "name": "Long mountain session", "intensity": "endurance", "minutes": 180, "key": True, "sport": training_sport},
            {"weekday": (long_day + 1) % 7, "name": "Recovery", "intensity": "recovery", "minutes": 40, "sport": "running" if sport == "trail_running" else training_sport},
        ]
    if sport == "climbing":
        return [
            {"weekday": 1, "name": "Climbing session", "intensity": "steady", "minutes": 90, "sport": "climbing", "key": True},
            {"weekday": 3, "name": "Aerobic run", "intensity": "easy", "minutes": 60, "sport": "running"},
            {"weekday": long_day, "name": "Long mountain aerobic", "intensity": "endurance", "minutes": 150, "sport": "hiking", "key": True},
            {"weekday": (long_day + 1) % 7, "name": "Climbing technique", "intensity": "easy", "minutes": 75, "sport": "climbing"},
        ]
    # running default
    return [
        {"weekday": 1, "name": "Threshold intervals", "intensity": "threshold", "minutes": 60, "key": True},
        {"weekday": 2, "name": "Easy run", "intensity": "easy", "minutes": 50},
        {"weekday": 4, "name": "Easy run + strides", "intensity": "easy", "minutes": 45},
        {"weekday": long_day, "name": "Long run", "intensity": "endurance", "minutes": 120, "key": True},
        {"weekday": (long_day + 1) % 7, "name": "Recovery run", "intensity": "recovery", "minutes": 40},
    ]



# Most weeks are not part of a race build. Rather than pretending a plan exists, a
# targetless week states its intent and derives it from current state.
INTENT_FACTORS = {"recover": 0.62, "maintain": 1.0, "build": 1.08}
RECOVER_FORM = -20.0
BUILD_FORM = 0.0


def choose_intent(context: dict) -> tuple[str, str]:
    """Pick an intent for a week with no objective, from form and recent load.

    Returns (intent, reason). Deterministic and explainable: the athlete should be able
    to see why the week looks the way it does.
    """
    state = context.get("state") or {}
    form = float(state.get("form") or 0)
    load_7 = float(state.get("load_7d") or 0)
    typical = float(state.get("typical_weekly_load_8w") or 0)

    if form <= RECOVER_FORM:
        return "recover", f"form is {form:.0f}, so fatigue is well ahead of fitness; this week protects recovery"
    if typical and load_7 > typical * 1.25:
        return "recover", f"the last 7 days carried {load_7:.0f} load against a typical {typical:.0f}; this week backs off"
    if form >= BUILD_FORM and typical and load_7 < typical * 0.9:
        return "build", f"form is {form:.0f} and recent load is below your norm, so there is room to add a little"
    return "maintain", f"form is {form:.0f} and recent load is close to your norm, so this week holds steady"


def generate_week_seed(context: dict, week_start: date, objective_id: int | None = None, strategy: str = "balanced",
                       target_hours: float | None = None, phase: str | None = None,
                       intent: str | None = None) -> tuple[str, list[dict]]:
    week_start = week_start - timedelta(days=week_start.weekday())
    objective = _primary_objective(context, objective_id)
    sport = _primary_sport(context, objective)
    profile = context.get("profile") or {}
    long_day = int(profile.get("preferred_long_day") if profile.get("preferred_long_day") is not None else 5)
    rest_day = profile.get("preferred_rest_day")
    block = context.get("current_block") or {}
    targets = block.get("targets") or {}
    # An explicit per-week target from the season plan wins over anything inferred.
    explicit_hours = float(target_hours) if target_hours else None

    typical_h = float(context.get("state", {}).get("typical_weekly_hours_8w") or 0)
    # A block's weekly_load target is honoured as well as weekly_hours. The Season UI
    # writes weekly_load, and reading only weekly_hours meant the target an athlete
    # typed into a training block had no effect on anything the planner produced.
    target_load = float(targets.get("weekly_load") or 0)
    typical_load = float(context.get("state", {}).get("typical_weekly_load_8w") or 0)
    implied_h = 0.0
    if target_load > 0 and typical_load > 0 and typical_h > 0:
        # Convert the load target into hours using the athlete's own recent load-per-hour,
        # so the two target styles cannot disagree about the same week.
        implied_h = target_load / (typical_load / typical_h)
    target_h = float(
        targets.get("weekly_hours")
        or implied_h
        or profile.get("available_hours_per_week")
        or (typical_h * 1.03 if typical_h else (8 if sport == "cycling" else 6))
    )
    phase = str(phase or block.get("type") or "base").lower()
    phase_factor = {"foundation": 0.85, "recovery": 0.7, "taper": 0.65, "peak": 0.9, "build": 1.05, "specific": 1.05}.get(phase, 1.0)
    intent_reason = None
    if explicit_hours:
        # The season plan has already applied phase shaping and recovery weeks, so the
        # phase factor must not be applied a second time.
        target_h = explicit_hours
    elif not objective and not block:
        # No race and no block: this is a general-fitness week, shaped by current state
        # rather than by a phase it is not in.
        chosen = intent if intent in INTENT_FACTORS else None
        if chosen is None:
            chosen, intent_reason = choose_intent(context)
        else:
            intent_reason = f"requested a {chosen} week"
        target_h *= INTENT_FACTORS[chosen]
        phase = chosen
    else:
        target_h *= phase_factor
    if profile.get("available_hours_per_week"):
        target_h = min(target_h, float(profile["available_hours_per_week"]))

    template = _template(sport, long_day)
    base_h = sum(x["minutes"] for x in template) / 60
    scale = max(0.65, min(1.7, target_h / max(base_h, 0.1)))
    if strategy == "conservative":
        scale *= 0.9

    existing_dates = {p["date"] for p in context.get("planned") or [] if week_start.isoformat() <= p["date"] <= (week_start + timedelta(days=6)).isoformat()}
    commands: list[dict] = []
    occupied = set(existing_dates)
    now_day = date.fromisoformat(context["as_of"])
    vertical_target = float(targets.get("vertical_m") or 0)

    for spec in template:
        wd = int(spec["weekday"])
        if rest_day is not None and wd == int(rest_day):
            wd = (wd + 1) % 7
        day = week_start + timedelta(days=wd)
        if day < now_day or day.isoformat() in occupied:
            continue
        minutes = max(30, round(spec["minutes"] * scale / 5) * 5)
        allowed, _ = _constraint_for_day(context, day, minutes / 60)
        if not allowed:
            # Search up to three nearby days while preserving key-session spacing.
            found = None
            for delta in (1, -1, 2, -2, 3):
                candidate = day + timedelta(days=delta)
                if candidate < week_start or candidate > week_start + timedelta(days=6) or candidate < now_day:
                    continue
                if candidate.isoformat() in occupied or (rest_day is not None and candidate.weekday() == int(rest_day)):
                    continue
                ok, _ = _constraint_for_day(context, candidate, minutes / 60)
                if ok:
                    found = candidate
                    break
            if not found:
                continue
            day = found
        occupied.add(day.isoformat())
        scheduled = datetime.combine(day, time(9, 0) if day.weekday() >= 5 else time(18, 0))
        duration_s = float(minutes * 60)
        workout_sport = spec.get("sport") or sport
        elevation = None
        if vertical_target and "long" in spec["name"].lower():
            elevation = round(vertical_target * 0.45)
        commands.append({
            "action": "create_workout",
            "scheduled_at": scheduled.isoformat(),
            "sport": workout_sport,
            "name": spec["name"],
            "duration_s": duration_s,
            "elevation_m": elevation,
            "intensity": spec["intensity"],
            "steps": _steps(spec["intensity"], duration_s),
            "objective_id": objective.get("id") if objective else None,
            "block_id": block.get("id") if block else None,
            "reason": f"{phase.title()}-phase {spec['intensity']} session for {objective['name'] if objective else sport + ' development'}." + (" Key session." if spec.get("key") else ""),
        })

    estimated_load = sum(estimate_planned_load(c.get("duration_s"), c.get("steps"), c.get("intensity")).load for c in commands)
    total_hours = sum(float(c.get("duration_s") or 0) for c in commands) / 3600
    objective_text = f" toward {objective['name']}" if objective else ""
    if intent_reason:
        objective_text = f" as a {phase} week: {intent_reason}"
    summary = f"Proposed {len(commands)} sessions ({total_hours:.1f} h, about {estimated_load:.0f} planned load){objective_text}. Existing workouts and hard availability constraints are preserved."
    return summary, commands



def generate_block_seed(context: dict, first_week: date, weeks: int, blocks: list[dict],
                        objective_id: int | None = None, strategy: str = "balanced") -> tuple[str, list[dict]]:
    """Generate several weeks in one pass, each shaped by its own season-plan target.

    Generating a week at a time produced a sequence of unrelated weeks: each one
    re-derived its target from the same 8-week median, so there was no progression, no
    recovery week and no taper. Here every week takes the target the periodisation
    assigned it, and already-planned days are left alone.
    """
    from app.planning.season import week_target

    first_week = first_week - timedelta(days=first_week.weekday())
    commands: list[dict] = []
    covered: list[str] = []
    # `planned` grows as we go, so later weeks see the workouts earlier weeks added and
    # do not double-book a day.
    working = dict(context)
    working["planned"] = list(context.get("planned") or [])

    for offset in range(max(1, weeks)):
        start = first_week + timedelta(weeks=offset)
        target = week_target(blocks, start)
        summary, week_commands = generate_week_seed(
            working, start, objective_id, strategy,
            target_hours=target.get("target_hours") if target else None,
            phase=target.get("phase") if target else None,
        )
        if not week_commands:
            continue
        commands.extend(week_commands)
        working["planned"] = working["planned"] + [
            {"id": -1, "date": c["scheduled_at"][:10], "scheduled_at": c["scheduled_at"],
             "sport": c["sport"], "name": c["name"], "duration_s": c.get("duration_s"),
             "projected_load": 0, "intensity": c.get("intensity"), "locked": False,
             "matched_activity_id": None}
            for c in week_commands
        ]
        phase = (target or {}).get("phase", "base")
        covered.append(f"{start.isoformat()} ({phase}{', recovery' if (target or {}).get('is_recovery') else ''})")

    total_hours = sum(float(c.get("duration_s") or 0) for c in commands) / 3600
    estimated = sum(estimate_planned_load(c.get("duration_s"), c.get("steps"), c.get("intensity")).load for c in commands)
    summary = (
        f"Proposed {len(commands)} sessions across {len(covered)} weeks "
        f"({total_hours:.1f} h, about {estimated:.0f} planned load). "
        f"Each week uses its periodised target, so progression, recovery weeks and the taper are preserved. "
        f"Existing workouts and hard availability constraints are untouched."
    )
    return summary, commands

def adapt_week_seed(context: dict, week_start: date) -> tuple[str, list[dict]]:
    week_start = week_start - timedelta(days=week_start.weekday())
    week_end = week_start + timedelta(days=6)
    today = date.fromisoformat(context["as_of"])
    planned = [p for p in context.get("planned") or [] if week_start.isoformat() <= p["date"] <= week_end.isoformat()]
    actual = [a for a in context.get("recent_activities") or [] if week_start.isoformat() <= a["date"] <= week_end.isoformat()]
    typical = float(context.get("state", {}).get("typical_weekly_load_8w") or 0)
    actual_load = sum(float(a.get("load") or 0) for a in actual)
    future = [p for p in planned if p["date"] >= today.isoformat()]
    future_load = sum(float(p.get("projected_load") or 0) for p in future)
    commands: list[dict] = []
    reasons = []

    # If a recent unexpectedly large day is followed by a hard session within 48h,
    # soften the next hard session rather than trying to preserve every planned load unit.
    recent_cutoff = today - timedelta(days=2)
    hard_recent = [a for a in actual if date.fromisoformat(a["date"]) >= recent_cutoff and float(a.get("load") or 0) >= max(110.0, typical * 0.25 if typical else 110.0)]
    if hard_recent:
        trigger = max(hard_recent, key=lambda x: float(x.get("load") or 0))
        trigger_day = date.fromisoformat(trigger["date"])
        candidates = [p for p in future if p.get("intensity") in HARD and 0 <= (date.fromisoformat(p["date"]) - trigger_day).days <= 2 and not p.get("locked")]
        if candidates:
            w = sorted(candidates, key=lambda x: x["scheduled_at"])[0]
            new_duration = max(30 * 60, float(w.get("duration_s") or 3600) * 0.75)
            commands.append({"action": "update_workout", "workout_id": w["id"], "duration_s": new_duration, "intensity": "easy", "steps": _steps("easy", new_duration), "reason": f"Recent {trigger.get('sport')} session added {float(trigger.get('load') or 0):.0f} load; reduce the next quality session to protect recovery."})
            reasons.append("softened a nearby quality session after an unexpectedly large recent load")

    projected_total = actual_load + future_load
    if typical and projected_total > typical * 1.15:
        # Reduce one non-key aerobic session if not already modified.
        already = {c.get("workout_id") for c in commands}
        easy = [p for p in future if p["id"] not in already and p.get("intensity") in {"easy", "endurance", "recovery"} and not p.get("locked")]
        if easy:
            w = sorted(easy, key=lambda x: float(x.get("projected_load") or 0), reverse=True)[0]
            new_duration = max(25 * 60, float(w.get("duration_s") or 3600) * 0.8)
            commands.append({"action": "update_workout", "workout_id": w["id"], "duration_s": new_duration, "intensity": w.get("intensity") or "easy", "reason": f"Current-week actual + remaining planned load is {projected_total:.0f}, above the recent typical week of {typical:.0f}; trim low-priority volume instead of removing the key session."})
            reasons.append("trimmed low-priority volume because projected weekly load is above the recent norm")

    missed = [p for p in planned if p["date"] < today.isoformat() and not p.get("matched_activity_id")]
    if missed:
        reasons.append(f"left {len(missed)} missed past session(s) in the past rather than making up the volume")

    if commands:
        summary = "Adaptation proposal: " + "; ".join(reasons) + "."
    else:
        summary = "No calendar changes are recommended from the current deterministic checks. The week does not show a clear load/spacing issue that justifies changing unlocked future sessions."
    return summary, commands
