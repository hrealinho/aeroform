"""Prescription units, pace/power targets, and the session library."""
from datetime import date, timedelta

import pytest

from app.ai.planner import generate_week_seed
from app.planning import sessions
from app.planning.prescription import (
    SPEED_FRACTIONS, hms, pace_string, prescribe, prescription_unit, target_for,
)
from app.planning.sessions import SESSION_LIBRARY, render, session_total_distance, session_total_seconds
from app.planning.workouts import estimate_planned_load, infer_intensity

RUN_TH = {"threshold_speed_mps": 1000 / 240}      # 4:00/km threshold
BIKE_TH = {"ftp": 250}


# --- pace formatting -------------------------------------------------------

def test_pace_seconds_carry_into_minutes():
    """Regression: 299.7s/km formatted as "4:60" because seconds were rounded after
    splitting rather than before."""
    assert pace_string(1000 / 299.7) == "5:00"
    assert pace_string(1000 / 240) == "4:00"
    assert pace_string(1000 / 245) == "4:05"
    assert pace_string(0) is None
    assert pace_string(None) is None


def test_hms_formats_hours_only_when_present():
    assert hms(2820) == "47:00"
    assert hms(3661) == "1:01:01"
    assert hms(0) == "0:00"


# --- targets ---------------------------------------------------------------

def test_running_targets_are_pace_bands_and_get_faster_with_intensity():
    order = ["recovery", "easy", "endurance", "steady", "tempo", "threshold", "vo2", "anaerobic"]
    speeds = [target_for(i, "running", RUN_TH)["min_speed_mps"] for i in order]
    # Faster intensity means higher speed, i.e. a lower pace number.
    assert speeds == sorted(speeds)
    threshold = target_for("threshold", "running", RUN_TH)
    assert threshold["kind"] == "pace"
    assert threshold["display"].endswith("/km")
    assert threshold["source"] == "threshold_speed_mps"


def test_cycling_targets_are_power_bands():
    target = target_for("threshold", "cycling", BIKE_TH)
    assert target["kind"] == "power"
    assert target["min_watts"] < 250 < target["max_watts"] + 1
    assert target["display"].endswith(" W")


def test_no_threshold_means_no_target_rather_than_an_invented_one():
    assert target_for("threshold", "running", {}) is None
    assert target_for("threshold", "cycling", {}) is None
    # An unknown intensity also gets nothing.
    assert target_for("nonsense", "running", RUN_TH) is None


def test_every_intensity_the_planner_uses_has_a_band():
    from app.planning.workouts import INTENSITY_FACTORS
    assert set(INTENSITY_FACTORS) <= set(SPEED_FRACTIONS)


# --- unit choice and conversion -------------------------------------------

def test_both_units_are_always_populated():
    """Time and distance are the same session described two ways, so a device export can
    use whichever it needs."""
    p = prescribe("threshold", "running", RUN_TH, unit="distance", duration_s=8 * 60)
    assert p.distance_m and p.duration_s
    assert p.unit == "distance"
    q = prescribe("easy", "running", RUN_TH, unit="time", distance_m=10000)
    assert q.duration_s and q.distance_m
    assert q.unit == "time"


def test_distance_prescriptions_round_to_human_numbers():
    p = prescribe("threshold", "running", RUN_TH, unit="distance", duration_s=8 * 60)
    assert p.distance_m % 100 == 0
    long = prescribe("endurance", "running", RUN_TH, unit="distance", duration_s=120 * 60)
    assert long.distance_m % 500 == 0


def test_conversion_still_works_without_thresholds():
    p = prescribe("easy", "running", {}, unit="distance", duration_s=45 * 60)
    assert p.distance_m > 0
    assert p.target is None      # honest: no threshold, no target


@pytest.mark.parametrize(("sport", "expected"), [
    ("running", "distance"), ("trail_running", "distance"), ("cycling", "time"), ("hiking", "time"),
])
def test_auto_unit_follows_how_each_sport_is_written(sport, expected):
    assert prescription_unit({"preferences": {}}, sport) == expected


def test_an_explicit_preference_overrides_the_default():
    assert prescription_unit({"preferences": {"prescription": "time"}}, "running") == "time"
    assert prescription_unit({"preferences": {"prescription": "distance"}}, "cycling") == "distance"
    assert prescription_unit(None, "running") == "distance"


# --- session library -------------------------------------------------------

def test_every_session_renders_close_to_its_target_duration():
    for key, spec in SESSION_LIBRARY.items():
        if key == "rest":
            continue
        target = max(spec.min_duration_s, 60 * 60)
        for unit in ("time", "distance"):
            total = session_total_seconds(render(spec, "running", target, RUN_TH, unit=unit))
            assert abs(total - target) / target < 0.06, f"{key} in {unit}: {total/60:.0f} vs {target/60:.0f}min"


def test_interval_sessions_carry_a_target_on_the_work_step():
    steps = render(SESSION_LIBRARY["km_repeats"], "running", 60 * 60, RUN_TH, unit="distance")
    repeat = next(s for s in steps if s["type"] == "repeat")
    work = next(s for s in repeat["steps"] if s["type"] == "work")
    assert work["distance_m"] == 1000
    assert work["target"]["kind"] == "pace"


def test_interval_volume_is_capped_rather_than_scaling_without_limit():
    """Regression: a high weekly target produced 11x1km. Extra weekly volume belongs in
    easy running, not in more reps."""
    spec = SESSION_LIBRARY["km_repeats"]
    huge = render(spec, "running", 3 * 3600, RUN_TH, unit="distance")
    repeat = next(s for s in huge if s["type"] == "repeat")
    assert repeat["repeat"] <= min(spec.repeat * 2, spec.repeat + 4)


def test_a_progressive_long_run_steps_down_in_pace():
    steps = render(SESSION_LIBRARY["progressive_long_run"], "running", 120 * 60, RUN_TH, unit="distance")
    intensities = [s["intensity"] for s in steps]
    assert intensities == ["easy", "endurance", "tempo"]


def test_continuous_and_structured_sessions_are_distinguishable():
    """The whole point: an easy run and a threshold session are no longer the same shape."""
    easy = render(SESSION_LIBRARY["easy_run"], "running", 60 * 60, RUN_TH)
    intervals = render(SESSION_LIBRARY["km_repeats"], "running", 60 * 60, RUN_TH)
    assert len(easy) == 1
    assert any(s["type"] == "repeat" for s in intervals)


def test_the_picker_matches_intensity_to_a_sensible_session():
    assert sessions.pick("recovery", "running", 40 * 60).key == "recovery_run"
    assert sessions.pick("endurance", "running", 120 * 60).key == "long_run"
    assert sessions.pick("threshold", "running", 60 * 60).key == "km_repeats"
    assert sessions.pick("threshold", "trail_running", 60 * 60).key == "uphill_threshold"
    assert sessions.pick("endurance", "hiking", 120 * 60).key == "long_mountain_session"
    # A short slot cannot hold an interval session, so it falls back rather than
    # rendering a session that does not fit.
    assert sessions.pick("threshold", "running", 20 * 60).key == "easy_run"


def test_an_explicit_preference_is_honoured_when_it_fits():
    assert sessions.pick("tempo", "running", 60 * 60, prefer="on_off_ks").key == "on_off_ks"
    # But not when the slot is too short for it.
    assert sessions.pick("tempo", "running", 20 * 60, prefer="on_off_ks").key != "on_off_ks"


def test_distance_totals_expand_repeats():
    steps = render(SESSION_LIBRARY["km_repeats"], "running", 60 * 60, RUN_TH, unit="distance")
    repeat = next(s for s in steps if s["type"] == "repeat")
    per_set = sum(i["distance_m"] for i in repeat["steps"])
    warmups = sum(s["distance_m"] for s in steps if s["type"] != "repeat")
    assert session_total_distance(steps) == pytest.approx(warmups + repeat["repeat"] * per_set, abs=1)


# --- rest days -------------------------------------------------------------

def test_a_rest_day_is_zero_load_and_not_reported_as_a_recovery_session():
    rest = [{"type": "rest", "intensity": "recovery", "duration_s": 0}]
    assert infer_intensity(rest) == "rest"
    estimate = estimate_planned_load(0, rest, None)
    assert estimate.load == 0.0
    assert estimate.method == "rest"


# --- end to end through the planner ---------------------------------------

def _context(prescription="distance", **overrides):
    base = {
        "as_of": date.today().isoformat(),
        "history": {"activity_count": 300},
        "state": {"form": 2, "load_7d": 400, "load_28d": 1600,
                  "typical_weekly_load_8w": 400, "typical_weekly_hours_8w": 8},
        "sports_28d": {"running": {"load": 400}},
        "objectives": [], "constraints": [], "planned": [], "current_block": None,
        "profile": {"preferred_long_day": 5, "preferred_rest_day": 6,
                    "preferences": {"prescription": prescription}},
        "threshold_values": RUN_TH,
    }
    base.update(overrides)
    return base


def test_generated_sessions_carry_distance_and_pace_when_asked():
    _, commands = generate_week_seed(_context("distance"), date.today() + timedelta(days=7))
    assert commands
    quality = next(c for c in commands if c["intensity"] in {"threshold", "tempo"})
    repeat = next(s for s in quality["steps"] if s.get("type") == "repeat")
    work = next(s for s in repeat["steps"] if s["type"] == "work")
    assert work["distance_m"] > 0
    assert "km" in work["target"]["display"]
    # The command itself reports a total distance, so a calendar can show "13km".
    assert quality["distance_m"] > 0


def test_the_same_week_in_time_units_carries_no_invented_distance_emphasis():
    _, commands = generate_week_seed(_context("time"), date.today() + timedelta(days=7))
    quality = next(c for c in commands if c["intensity"] in {"threshold", "tempo"})
    repeat = next(s for s in quality["steps"] if s.get("type") == "repeat")
    work = next(s for s in repeat["steps"] if s["type"] == "work")
    # Time is authoritative, so the work step is a round number of seconds.
    assert work["duration_s"] % 5 == 0
    assert work["target"]["kind"] == "pace"


def test_sessions_are_named_from_the_library_not_generically():
    _, commands = generate_week_seed(_context(), date.today() + timedelta(days=7))
    names = {c["name"] for c in commands}
    assert names & {"1km repeats", "Tempo 3-2-1", "On-off kilometres", "Long run"}
    assert "Threshold intervals" not in names       # the old generic label


def test_recovery_sessions_stay_short_however_big_the_week_is():
    """Regression: scaling a nine-hour week turned a recovery run into 61 minutes."""
    context = _context()
    context["profile"]["available_hours_per_week"] = 12
    _, commands = generate_week_seed(context, date.today() + timedelta(days=7))
    recovery = [c for c in commands if c["intensity"] == "recovery"]
    assert recovery
    assert all(c["duration_s"] <= 46 * 60 for c in recovery)


def test_a_downgraded_session_is_described_honestly():
    """Regression: a slot too short for the requested session rendered an easy run but
    still reported intensity "tempo", so the name and the intensity disagreed and the
    load was computed for a session nobody was doing."""
    context = _context()
    # A tiny weekly budget forces every slot below the interval minimums.
    context["profile"]["available_hours_per_week"] = 3
    _, commands = generate_week_seed(context, date.today() + timedelta(days=7))

    for c in commands:
        spec = sessions.SESSION_LIBRARY
        matching = [s for s in spec.values() if s.name == c["name"]]
        assert matching, f"unknown session name {c['name']}"
        assert matching[0].intensity == c["intensity"], (
            f"{c['name']} reported as {c['intensity']} but the session is {matching[0].intensity}")
        if "could not hold it" in (c.get("reason") or ""):
            assert "Key session" not in c["reason"]
