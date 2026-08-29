"""Periodisation, multi-week generation, and targetless general-fitness weeks.

Covers the layer that was missing between an objective and individual workouts.
"""
from datetime import date, timedelta

import pytest

from app.ai.planner import INTENT_FACTORS, choose_intent, generate_block_seed, generate_week_seed
from app.planning.season import MAX_PEAK_MULTIPLE, periodise, week_target
from app.planning.workouts import estimate_planned_load


def _context(**overrides):
    base = {
        "as_of": "2026-09-01",
        "history": {"activity_count": 300},
        "state": {"fitness": 70, "fatigue": 70, "form": 0, "load_7d": 400, "load_28d": 1600,
                  "typical_weekly_load_8w": 400, "typical_weekly_hours_8w": 8},
        "sports_28d": {"trail_running": {"load": 400}},
        "objectives": [], "constraints": [], "planned": [], "current_block": None,
        "profile": {"preferred_long_day": 5, "preferred_rest_day": 0, "available_hours_per_week": None},
        "recent_activities": [], "recent_weeks": [], "recent_four_weeks": [], "prior_four_weeks": [],
        "adherence_28d_pct": None,
    }
    base.update(overrides)
    return base


# --- periodisation --------------------------------------------------------

def test_plan_runs_backwards_from_the_race_and_ends_in_a_taper():
    blocks = periodise(date(2026, 9, 1), date(2027, 2, 1), 400, 8, long_objective=True, objective_name="UTMB")
    assert blocks[-1].block_type == "taper"
    assert len(blocks[-1].weeks) == 3          # long objective gets three taper weeks
    assert [b.block_type for b in blocks] == ["base", "build", "specific", "peak", "taper"]
    assert blocks[0].start.weekday() == 0      # blocks start on a Monday


def test_short_objective_gets_a_shorter_taper():
    short = periodise(date(2026, 9, 1), date(2027, 2, 1), 400, 8, long_objective=False)
    long = periodise(date(2026, 9, 1), date(2027, 2, 1), 400, 8, long_objective=True)
    assert len(short[-1].weeks) == 2
    assert len(long[-1].weeks) == 3


def test_the_taper_actually_descends():
    """Regression: both taper weeks previously sat at the same 55% of peak, so the taper
    did not taper. Checked against a commercial marathon plan running roughly
    81% / 56% / 19% of peak volume across its three taper weeks."""
    blocks = periodise(date(2026, 7, 27), date(2026, 12, 6), 400, 8, long_objective=True)
    weeks = [w for b in blocks for w in b.weeks]
    peak = max(w.target_load for w in weeks)
    taper = [w for w in weeks if w.phase == "taper"]

    assert len(taper) == 3
    fractions = [w.target_load / peak for w in taper]
    # Strictly decreasing, starting high so volume comes off gradually.
    assert fractions[0] > fractions[1] > fractions[2]
    assert 0.70 <= fractions[0] <= 0.90
    assert fractions[-1] <= 0.40


def test_the_peak_phase_holds_the_highest_week():
    """Regression: "peak" sat at 0.85 while "specific" sat at 1.00, so the highest week
    landed in the wrong phase."""
    blocks = periodise(date(2026, 7, 27), date(2026, 12, 6), 400, 8, long_objective=True)
    weeks = [w for b in blocks for w in b.weeks]
    peak_week = max(weeks, key=lambda w: w.target_load)
    assert peak_week.phase == "peak"


@pytest.mark.parametrize(("weeks_out", "expected"), [(2, {"taper"}), (4, {"peak", "taper"})])
def test_short_horizons_collapse_gracefully(weeks_out, expected):
    """With three weeks left there is no point prescribing a base phase."""
    start = date(2026, 9, 7)
    blocks = periodise(start, start + timedelta(weeks=weeks_out), 400, 8)
    assert {b.block_type for b in blocks} == expected


def test_no_time_means_no_plan():
    assert periodise(date(2026, 9, 1), date(2026, 9, 2), 400, 8) == []


def test_load_ramps_but_never_beyond_the_cap():
    blocks = periodise(date(2026, 9, 1), date(2027, 6, 1), 400, 8)
    loads = [w.target_load for b in blocks for w in b.weeks]
    assert max(loads) <= 400 * MAX_PEAK_MULTIPLE + 0.1
    # And it genuinely progresses rather than sitting flat.
    assert max(loads) > 400


def test_every_fourth_loading_week_is_a_recovery_week():
    blocks = periodise(date(2026, 9, 1), date(2027, 4, 1), 400, 8)
    weeks = [w for b in blocks for w in b.weeks]
    recovery = [w for w in weeks if w.is_recovery]
    assert recovery
    assert all(w.phase in {"base", "build", "specific"} for w in recovery)
    for w in recovery:
        neighbours = [x.target_load for x in weeks if x.phase == w.phase and not x.is_recovery]
        assert w.target_load < min(neighbours)


def test_targets_scale_with_the_athletes_own_load():
    small = periodise(date(2026, 9, 1), date(2027, 2, 1), 200, 5)
    large = periodise(date(2026, 9, 1), date(2027, 2, 1), 800, 14)
    peak_small = max(w.target_load for b in small for w in b.weeks)
    peak_large = max(w.target_load for b in large for w in b.weeks)
    assert peak_large > peak_small * 3


def test_week_target_lookup_finds_the_right_week():
    blocks = [b.as_dict() for b in periodise(date(2026, 9, 7), date(2027, 2, 1), 400, 8)]
    found = week_target(blocks, date(2026, 9, 7))
    assert found and found["week_start"] == "2026-09-07"
    assert found["block_type"]
    assert week_target(blocks, date(2020, 1, 6)) is None


# --- multi-week generation ------------------------------------------------

def test_block_generation_follows_each_weeks_target():
    blocks = [b.as_dict() for b in periodise(date(2026, 9, 7), date(2027, 3, 1), 400, 8)]
    summary, commands = generate_block_seed(_context(), date(2026, 9, 7), 4, blocks)
    assert commands
    assert "4 weeks" in summary

    by_week: dict[str, float] = {}
    for c in commands:
        monday = (date.fromisoformat(c["scheduled_at"][:10]) - timedelta(days=date.fromisoformat(c["scheduled_at"][:10]).weekday())).isoformat()
        by_week[monday] = by_week.get(monday, 0.0) + estimate_planned_load(
            c.get("duration_s"), c.get("steps"), c.get("intensity")).load
    assert len(by_week) == 4
    # The fourth week is a recovery week, so it must be the lightest of the four.
    assert min(by_week, key=by_week.get) == "2026-09-28"


def test_block_generation_does_not_double_book_a_day():
    blocks = [b.as_dict() for b in periodise(date(2026, 9, 7), date(2027, 3, 1), 400, 8)]
    _, commands = generate_block_seed(_context(), date(2026, 9, 7), 3, blocks)
    days = [c["scheduled_at"][:10] for c in commands]
    assert len(days) == len(set(days))


def test_block_generation_preserves_existing_workouts():
    blocks = [b.as_dict() for b in periodise(date(2026, 9, 7), date(2027, 3, 1), 400, 8)]
    context = _context(planned=[{"id": 1, "date": "2026-09-08", "scheduled_at": "2026-09-08T18:00:00",
                                 "sport": "running", "name": "mine", "duration_s": 3600,
                                 "projected_load": 40, "intensity": "easy", "locked": True,
                                 "matched_activity_id": None}])
    _, commands = generate_block_seed(context, date(2026, 9, 7), 2, blocks)
    assert all(c["scheduled_at"][:10] != "2026-09-08" for c in commands)


# --- targetless general-fitness weeks -------------------------------------

@pytest.mark.parametrize(("form", "load_7", "expected"), [
    (-30, 400, "recover"),   # fatigue well ahead of fitness
    (0, 600, "recover"),     # last 7 days far above the norm
    (5, 300, "build"),       # fresh with room to add
    (0, 400, "maintain"),    # steady
])
def test_intent_is_chosen_from_current_state(form, load_7, expected):
    intent, reason = choose_intent(_context(state={
        "form": form, "load_7d": load_7, "typical_weekly_load_8w": 400, "typical_weekly_hours_8w": 8,
    }))
    assert intent == expected
    assert reason  # always explainable


def test_targetless_week_is_planned_and_explains_itself():
    """"Plan my next week" with no race and no block."""
    summary, commands = generate_week_seed(_context(), date(2026, 9, 7))
    assert commands
    assert "maintain week" in summary
    assert "close to your norm" in summary


def test_recover_intent_produces_a_lighter_week_than_build():
    def total(intent):
        _, commands = generate_week_seed(_context(), date(2026, 9, 7), intent=intent)
        return sum(estimate_planned_load(c.get("duration_s"), c.get("steps"), c.get("intensity")).load
                   for c in commands)
    assert total("recover") < total("maintain") < total("build")
    assert set(INTENT_FACTORS) == {"recover", "maintain", "build"}


def test_an_objective_still_takes_precedence_over_intent():
    context = _context(objectives=[{"id": 1, "name": "Marathon", "date": "2026-12-01", "days_away": 90,
                                    "sport": "running", "priority": "A", "elevation_m": 0}])
    summary, _ = generate_week_seed(context, date(2026, 9, 7))
    assert "toward Marathon" in summary


# --- API ------------------------------------------------------------------

def _objective(client, days_ahead=150, **kw):
    body = {"name": "Target race", "event_date": (date.today() + timedelta(days=days_ahead)).isoformat(),
            "sport": "trail_running", "priority": "A"}
    body.update(kw)
    return client.post("/api/v1/objectives", json=body).json()


def test_plan_season_previews_without_creating_blocks(client, session, athlete):
    from app.domain.models import TrainingBlock

    _objective(client)
    body = client.post("/api/v1/coach/plan-season", json={"apply": False}).json()
    assert body["total_weeks"] > 10
    assert body["blocks"][-1]["block_type"] == "taper"
    assert body["applied"] == []
    assert session.query(TrainingBlock).count() == 0


def test_plan_season_apply_creates_blocks_and_audits(client, session, athlete):
    from app.domain.models import PlanChangeAudit, TrainingBlock

    from app.domain.models import PlannedWorkout

    objective = _objective(client)
    body = client.post("/api/v1/coach/plan-season", json={"apply": True}).json()
    assert session.query(TrainingBlock).count() == len(body["blocks"])
    audit = session.query(PlanChangeAudit).filter_by(entity_type="season_plan").one()
    assert audit.after_state["blocks"]

    # The race itself is part of the plan: applied covers the blocks plus the race.
    assert len(body["applied"]) == len(body["blocks"]) + 1
    race = session.query(PlannedWorkout).filter_by(objective_id=objective["id"]).one()
    assert race.locked is True                    # the date is not the planner's to move
    assert race.name == "Target race"
    assert race.projected_load > 0


def test_reapplying_does_not_duplicate_the_race(client, session, athlete):
    from app.domain.models import PlannedWorkout

    objective = _objective(client)
    client.post("/api/v1/coach/plan-season", json={"apply": True})
    client.post("/api/v1/coach/plan-season", json={"apply": True})
    assert session.query(PlannedWorkout).filter_by(objective_id=objective["id"]).count() == 1


def test_reapplying_a_plan_replaces_rather_than_stacks(client, session, athlete):
    from app.domain.models import TrainingBlock

    _objective(client)
    client.post("/api/v1/coach/plan-season", json={"apply": True})
    first = session.query(TrainingBlock).count()
    client.post("/api/v1/coach/plan-season", json={"apply": True})
    assert session.query(TrainingBlock).count() == first


def test_plan_season_needs_an_objective(client):
    response = client.post("/api/v1/coach/plan-season", json={"apply": False})
    assert response.status_code == 409
    assert "objective" in response.json()["detail"].lower()


def test_generate_block_creates_one_multi_week_proposal(client, session, athlete):
    _objective(client)
    client.post("/api/v1/coach/plan-season", json={"apply": True})
    body = client.post("/api/v1/coach/generate-block", json={"weeks": 4}).json()
    assert body["proposal_type"] == "generate_block"
    assert body["validation"]["valid"] is True
    weeks = {c["scheduled_at"][:7] for c in body["commands"]}
    assert body["commands"] and len(body["commands"]) > 8
    assert weeks

    applied = client.post(f"/api/v1/coach/proposals/{body['id']}/approve")
    assert applied.status_code == 200
    assert len(applied.json()["applied"]) == len(body["commands"])


def test_block_progress_compares_target_planned_and_actual(client, session, athlete):
    _objective(client)
    client.post("/api/v1/coach/plan-season", json={"apply": True})
    proposal = client.post("/api/v1/coach/generate-block", json={"weeks": 2}).json()
    client.post(f"/api/v1/coach/proposals/{proposal['id']}/approve")

    body = client.get("/api/v1/analytics/block-progress").json()
    assert body["blocks"]
    weeks = [w for b in body["blocks"] for w in b["weeks"]]
    assert any(w["planned_load"] > 0 for w in weeks)
    covered = [w for w in weeks if w["planned_load"] > 0]
    assert all(w["planned_vs_target_pct"] is not None for w in covered)


def test_block_progress_is_empty_without_blocks(client):
    body = client.get("/api/v1/analytics/block-progress").json()
    assert body["blocks"] == []
    assert "objective" in body["note"]


# --- plans built without history ------------------------------------------

def test_a_plan_without_history_uses_a_labelled_default(client, session, athlete):
    """Regression: with no activities, typical weekly load is 0, so every target
    collapsed to ~1 load. The structure was right and the numbers meaningless, which is
    worse than refusing because it looks like a plan."""
    from app.planning.season import DEFAULT_WEEKLY_LOAD

    _objective(client)
    body = client.post("/api/v1/coach/plan-season", json={"apply": False}).json()

    assert body["basis"]["using_default"] is True
    assert "Not enough training history" in body["basis"]["warning"]
    assert body["basis"]["ramping_from_load"] == DEFAULT_WEEKLY_LOAD

    peak = max(b["targets"]["peak_weekly_load"] for b in body["blocks"])
    assert peak > 100, f"targets must be usable, got peak {peak}"


def test_real_history_is_preferred_over_the_default(client, session, athlete):
    from app.planning.season import resolve_basis

    load, hours, warning = resolve_basis(520.0, 9.5)
    assert (load, hours, warning) == (520.0, 9.5, None)


@pytest.mark.parametrize(("load", "hours"), [(0, 0), (10, 1), (39, 8), (400, 0)])
def test_unusable_history_falls_back(load, hours):
    from app.planning.season import DEFAULT_WEEKLY_LOAD, resolve_basis

    resolved_load, _, warning = resolve_basis(load, hours)
    assert resolved_load == DEFAULT_WEEKLY_LOAD
    assert warning is not None
