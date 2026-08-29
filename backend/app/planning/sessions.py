"""A library of named, structured sessions.

Previously the planner could build exactly two structured workouts - a 4x8min
threshold and a 5x3min VO2 - and everything else became one undifferentiated block.
So a long run, a recovery run and a marathon-pace tempo were structurally identical,
which makes a generated plan unusable: the athlete cannot tell what a session is for.

Sessions are declared as data rather than code branches, so adding one is a table
entry. Each declares its shape in *relative* terms and is rendered into concrete
steps at the athlete's target volume and in their chosen unit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.planning.prescription import prescribe

# Warm-up and cool-down as a fraction of total session time, bounded so a short
# session is not all warm-up and a long one does not warm up for half an hour.
WARMUP_FRACTION = 0.18
MIN_WARMUP_S = 8 * 60
MAX_WARMUP_S = 20 * 60


@dataclass(frozen=True)
class Rep:
    """One work/recovery pair inside a set."""

    work_s: float
    recovery_s: float
    work_intensity: str = "threshold"
    recovery_intensity: str = "easy"


@dataclass(frozen=True)
class SessionSpec:
    """A named session shape.

    ``reps`` are expressed in seconds at a reference pace; rendering converts them to
    the athlete's unit. ``scalable`` sets whether the rep count grows with the session's
    target duration - interval volume should scale, a fixed ladder should not.
    """

    key: str
    name: str
    intensity: str
    reps: list[Rep] = field(default_factory=list)
    repeat: int = 1
    scalable: bool = False
    progressive: bool = False
    description: str = ""
    min_duration_s: float = 0.0


# The vocabulary. Names describe what the athlete does, which is how a coach writes them.
SESSION_LIBRARY: dict[str, SessionSpec] = {
    "easy_run": SessionSpec(
        "easy_run", "Easy run", "easy",
        description="Steady aerobic running at a conversational effort.",
    ),
    "recovery_run": SessionSpec(
        "recovery_run", "Recovery run", "recovery",
        description="Deliberately slow. The point is circulation, not fitness.",
    ),
    "long_run": SessionSpec(
        "long_run", "Long run", "endurance",
        description="Time on feet. Aerobic throughout, finishing controlled.",
    ),
    "progressive_long_run": SessionSpec(
        "progressive_long_run", "Progressive long run", "endurance", progressive=True,
        description="Starts easy and steps down in pace, finishing at or near tempo.",
        min_duration_s=60 * 60,
    ),
    "race_practice_long_run": SessionSpec(
        "race_practice_long_run", "Race practice long run", "endurance",
        reps=[Rep(20 * 60, 10 * 60, "race", "endurance")], repeat=3, scalable=True,
        description="Blocks at race pace inside a long run, to rehearse pacing and fuelling.",
        min_duration_s=90 * 60,
    ),
    "km_repeats": SessionSpec(
        "km_repeats", "1km repeats", "threshold",
        reps=[Rep(4 * 60, 90, "threshold", "recovery")], repeat=5, scalable=True,
        description="Classic threshold repeats with short jog recoveries.",
        min_duration_s=40 * 60,
    ),
    "tempo_3_2_1": SessionSpec(
        "tempo_3_2_1", "Tempo 3-2-1", "tempo",
        reps=[Rep(12 * 60, 3 * 60, "tempo", "easy"),
              Rep(8 * 60, 2 * 60, "tempo", "easy"),
              Rep(4 * 60, 2 * 60, "threshold", "easy")],
        description="A descending ladder: the efforts shorten as the pace lifts.",
        min_duration_s=45 * 60,
    ),
    "fast_8_4_2": SessionSpec(
        "fast_8_4_2", "Fast 8-4-2s", "threshold",
        reps=[Rep(8 * 60, 3 * 60, "threshold", "easy"),
              Rep(4 * 60, 2 * 60, "vo2", "easy"),
              Rep(2 * 60, 2 * 60, "vo2", "easy")],
        description="Descending intervals finishing well above threshold.",
        min_duration_s=45 * 60,
    ),
    "rolling_400s": SessionSpec(
        "rolling_400s", "Rolling 400s", "vo2",
        reps=[Rep(95, 95, "vo2", "recovery")], repeat=10, scalable=True,
        description="Short, sharp repetitions with equal jog recovery.",
        min_duration_s=35 * 60,
    ),
    "broken_miles": SessionSpec(
        "broken_miles", "Broken miles", "threshold",
        reps=[Rep(4 * 60 + 45, 30, "threshold", "recovery"),
              Rep(95, 2 * 60, "vo2", "recovery")], repeat=3, scalable=True,
        description="A mile split into a threshold block plus a fast finish.",
        min_duration_s=45 * 60,
    ),
    "on_off_ks": SessionSpec(
        "on_off_ks", "On-off kilometres", "tempo",
        reps=[Rep(4 * 60, 4 * 60, "threshold", "endurance")], repeat=5, scalable=True,
        description="Alternating hard and steady kilometres, never fully recovering.",
        min_duration_s=45 * 60,
    ),
    "race_pace_fartlek": SessionSpec(
        "race_pace_fartlek", "Race pace fartlek", "race",
        reps=[Rep(5 * 60, 2 * 60, "race", "easy")], repeat=4, scalable=True,
        description="Race-pace blocks with rolling recoveries. Sharpening, not loading.",
        min_duration_s=35 * 60,
    ),
    "uphill_threshold": SessionSpec(
        "uphill_threshold", "Uphill threshold", "threshold",
        reps=[Rep(6 * 60, 4 * 60, "threshold", "recovery")], repeat=5, scalable=True,
        description="Sustained climbing efforts, descending easy to recover.",
        min_duration_s=45 * 60,
    ),
    "long_mountain_session": SessionSpec(
        "long_mountain_session", "Long mountain session", "endurance",
        description="Continuous vertical at an aerobic effort. Time on feet with load.",
    ),
    "rest": SessionSpec("rest", "Rest day", "recovery", description="No training. Recovery is the session."),
}


def _warmup_seconds(total_s: float) -> float:
    return min(MAX_WARMUP_S, max(MIN_WARMUP_S, total_s * WARMUP_FRACTION))


def _scaled_repeat(spec: SessionSpec, work_budget_s: float) -> int:
    """How many times to run the rep set so the work fits the available time."""
    if not spec.reps:
        return 1
    per_set = sum(r.work_s + r.recovery_s for r in spec.reps)
    if per_set <= 0:
        return spec.repeat
    if not spec.scalable:
        return spec.repeat
    # Floor, not round: rounding up overshoots the session's target duration, and the
    # weekly volume target is the sum of these durations.
    #
    # The cap matters as much as the fit. A high weekly target used to scale interval
    # count without limit, producing 11x1km - arithmetically consistent, but not a
    # session anyone prescribes. Interval volume has a ceiling; extra weekly volume
    # belongs in easy running, not in more reps.
    ceiling = min(spec.repeat * 2, spec.repeat + 4)
    return max(2, min(int(work_budget_s // per_set), ceiling))


def render(spec: SessionSpec, sport: str, total_duration_s: float, thresholds: dict,
           unit: str = "time") -> list[dict]:
    """Turn a session spec into concrete steps in the athlete's unit."""
    if spec.key == "rest":
        return [{"type": "rest", "intensity": "recovery", "duration_s": 0}]

    def step(kind: str, intensity: str, seconds: float) -> dict:
        p = prescribe(intensity, sport, thresholds, unit=unit, duration_s=seconds)
        return {"type": kind, "intensity": intensity, **p.as_step_fields()}

    # Continuous sessions: one block, or a progression.
    if not spec.reps and not spec.progressive:
        return [step("main", spec.intensity, total_duration_s)]

    if spec.progressive:
        # Thirds: easy, endurance, tempo. A progression the athlete can feel.
        third = total_duration_s / 3
        return [
            step("main", "easy", third),
            step("main", "endurance", third),
            step("main", "tempo", third),
        ]

    warmup = _warmup_seconds(total_duration_s)
    cooldown = _warmup_seconds(total_duration_s) * 0.7
    work_budget = max(total_duration_s - warmup - cooldown, 60.0)
    repeat = _scaled_repeat(spec, work_budget)

    inner: list[dict] = []
    for rep in spec.reps:
        inner.append(step("work", rep.work_intensity, rep.work_s))
        if rep.recovery_s > 0:
            inner.append(step("recovery", rep.recovery_intensity, rep.recovery_s))

    # A non-scalable ladder has a fixed work total, so whatever is left over is absorbed
    # by the cool-down. Otherwise a 60-minute slot renders as a 49-minute session and the
    # week silently comes in under its target.
    work_total = sum(r.work_s + r.recovery_s for r in spec.reps) * repeat
    leftover = total_duration_s - warmup - work_total - cooldown
    if leftover > 0:
        cooldown += leftover

    steps: list[dict] = [step("warmup", "easy", warmup)]
    if repeat > 1:
        steps.append({"type": "repeat", "repeat": repeat, "steps": inner})
    else:
        steps.extend(inner)
    steps.append(step("cooldown", "easy", cooldown))
    return steps


def session_total_seconds(steps: list[dict]) -> float:
    """Sum a rendered session, expanding repeats."""
    total = 0.0
    for s in steps or []:
        if s.get("type") == "repeat":
            total += (s.get("repeat") or 1) * session_total_seconds(s.get("steps") or [])
        else:
            total += float(s.get("duration_s") or 0)
    return total


def session_total_distance(steps: list[dict]) -> float:
    """Sum a rendered session's distance, expanding repeats. 0 when not distance-aware."""
    total = 0.0
    for s in steps or []:
        if s.get("type") == "repeat":
            total += (s.get("repeat") or 1) * session_total_distance(s.get("steps") or [])
        else:
            total += float(s.get("distance_m") or 0)
    return round(total)


def pick(intensity: str, sport: str, duration_s: float, prefer: str | None = None) -> SessionSpec:
    """Choose a session for an intensity, honouring an explicit preference when it fits."""
    if prefer and prefer in SESSION_LIBRARY:
        candidate = SESSION_LIBRARY[prefer]
        if duration_s >= candidate.min_duration_s:
            return candidate

    label = (intensity or "endurance").lower()
    mountain = sport in {"trail_running", "hiking", "mountaineering"}

    if label == "recovery":
        return SESSION_LIBRARY["recovery_run"]
    if label == "easy":
        return SESSION_LIBRARY["easy_run"]
    if label == "endurance":
        if mountain and duration_s >= 90 * 60:
            return SESSION_LIBRARY["long_mountain_session"]
        if duration_s >= 100 * 60:
            return SESSION_LIBRARY["long_run"]
        return SESSION_LIBRARY["easy_run"]
    if label in {"threshold", "tempo", "steady"}:
        if mountain:
            return SESSION_LIBRARY["uphill_threshold"]
        for key in ("km_repeats", "tempo_3_2_1", "on_off_ks"):
            if duration_s >= SESSION_LIBRARY[key].min_duration_s:
                return SESSION_LIBRARY[key]
        return SESSION_LIBRARY["easy_run"]
    if label in {"vo2", "anaerobic"}:
        for key in ("fast_8_4_2", "rolling_400s"):
            if duration_s >= SESSION_LIBRARY[key].min_duration_s:
                return SESSION_LIBRARY[key]
        return SESSION_LIBRARY["easy_run"]
    if label == "race":
        return SESSION_LIBRARY["race_pace_fartlek"]
    return SESSION_LIBRARY["easy_run"]
