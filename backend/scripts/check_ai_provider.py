#!/usr/bin/env python3
"""Exercise the configured AI provider against a real plan, and audit what it returned.

This is not a smoke test. The system prompt tells the model what it may not do -
change weekly volume materially, move sessions onto new days, add or remove sessions,
invent numbers - and none of that is enforced by the schema. This script checks each
rule against the actual response, so a provider that quietly ignores the contract is
visible rather than silently shaping plans.

Usage:
    AI_PROVIDER=openai OPENAI_API_KEY=... python scripts/check_ai_provider.py
    AI_PROVIDER=anthropic ANTHROPIC_API_KEY=... python scripts/check_ai_provider.py
    AI_PROVIDER=local python scripts/check_ai_provider.py        # no credentials needed
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

from app.ai.planner import generate_week_seed
from app.ai.provider import resolve_provider
from app.core.config import settings
from app.planning.sessions import session_total_seconds

# Deliberately synthetic and self-contained, so the check does not depend on whatever
# happens to be in the database.
CONTEXT = {
    "as_of": date.today().isoformat(),
    "history": {"activity_count": 420},
    "state": {"fitness": 74, "fatigue": 68, "form": 6, "load_7d": 380,
              "load_28d": 1620, "typical_weekly_load_8w": 420, "typical_weekly_hours_8w": 8.5},
    "sports_28d": {"running": {"load": 420, "hours": 8.5, "sessions": 5}},
    "objectives": [{"id": 1, "name": "Marathon", "date": (date.today() + timedelta(days=112)).isoformat(),
                    "days_away": 112, "sport": "running", "priority": "A", "elevation_m": 240}],
    "current_block": {"id": 1, "name": "Build", "type": "build", "targets": {"weekly_load": 460}},
    "constraints": [], "planned": [],
    "profile": {"available_hours_per_week": 9, "preferred_long_day": 5,
                "preferred_rest_day": 3, "doubles_allowed": False,
                "preferences": {"prescription": "distance"}},
    "threshold_values": {"threshold_speed_mps": 1000 / 245, "max_hr": 190, "resting_hr": 48},
    "recent_activities": [], "recent_weeks": [], "recent_four_weeks": [],
    "prior_four_weeks": [], "adherence_28d_pct": 86.0,
}

MAX_VOLUME_DRIFT = 0.10


def summarise(commands: list[dict]) -> list[str]:
    rows = []
    for c in sorted(commands, key=lambda x: str(x.get("scheduled_at"))):
        steps = c.get("steps") or []
        structure = ""
        for s in steps:
            if s.get("type") == "repeat":
                inner = s.get("steps") or []
                work = next((i for i in inner if i.get("type") == "work"), {})
                unit = f"{work.get('distance_m')}m" if work.get("distance_m") else f"{(work.get('duration_s') or 0)/60:.0f}min"
                structure = f"{s.get('repeat')}x{unit} @ {(work.get('target') or {}).get('display', work.get('intensity'))}"
                break
        mins = session_total_seconds(steps) / 60 or (c.get("duration_s") or 0) / 60
        rows.append(f"  {str(c.get('scheduled_at'))[:10]}  {c.get('name','?'):26s} "
                    f"{mins:5.0f}min  {c.get('intensity',''):10s} {structure}")
    return rows


def audit(seed: list[dict], refined: list[dict]) -> list[tuple[bool, str]]:
    """Check the refined plan against the rules the system prompt set."""
    seed_days = {str(c.get("scheduled_at"))[:10] for c in seed}
    refined_days = {str(c.get("scheduled_at"))[:10] for c in refined}
    seed_mins = sum(session_total_seconds(c.get("steps") or []) or (c.get("duration_s") or 0) for c in seed) / 60
    refined_mins = sum(session_total_seconds(c.get("steps") or []) or (c.get("duration_s") or 0) for c in refined) / 60
    drift = abs(refined_mins - seed_mins) / seed_mins if seed_mins else 0

    return [
        (len(refined) == len(seed), f"session count unchanged ({len(seed)} -> {len(refined)})"),
        (not (refined_days - seed_days), f"no new days used (added: {sorted(refined_days - seed_days) or 'none'})"),
        (drift <= MAX_VOLUME_DRIFT, f"volume within {MAX_VOLUME_DRIFT:.0%} ({seed_mins:.0f} -> {refined_mins:.0f}min, {drift:+.1%})"),
        (all(c.get("action") == "create_workout" for c in refined), "every command is still a create"),
        (all(c.get("steps") for c in refined), "every session still has steps"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise and audit the configured AI provider")
    parser.add_argument("--json", action="store_true", help="dump the refined commands")
    args = parser.parse_args()

    week = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
    seed_summary, seed = generate_week_seed(CONTEXT, week, objective_id=1)
    print(f"provider configured : {settings.ai_provider}")
    print(f"model               : {settings.ai_model}")
    print(f"\nSEED (deterministic, {len(seed)} sessions)\n{seed_summary}")
    print("\n".join(summarise(seed)))

    provider, error = resolve_provider()
    if error:
        print(f"\nPROVIDER UNAVAILABLE: {error}")
        print("The app would fall back to the seed above, which is a valid plan.")
        return 1

    print(f"\ncalling {getattr(provider, 'name', '?')} ...")
    try:
        plan = provider.refine_plan("generate_week", CONTEXT, seed_summary, seed)
    except Exception as exc:
        print(f"\nREFINEMENT FAILED: {exc}")
        print("The app would fall back to the seed above.")
        return 1

    print(f"\nREFINED by {plan.provider} ({len(plan.commands)} sessions)\n{plan.summary}")
    print("\n".join(summarise(plan.commands)))

    print("\nCONTRACT AUDIT")
    results = audit(seed, plan.commands)
    for ok, label in results:
        print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")

    if args.json:
        print("\n" + json.dumps(plan.commands, indent=2, default=str))

    failed = [label for ok, label in results if not ok]
    print(f"\n{'all contract rules held' if not failed else str(len(failed)) + ' rule(s) violated'}")
    print("Note: violations do not reach the calendar - validate_commands runs next - but they")
    print("show the model is not honouring the prompt, which is worth knowing.")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
