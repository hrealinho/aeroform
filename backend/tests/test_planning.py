from datetime import date, datetime, timedelta
from types import SimpleNamespace

from app.planning.workouts import estimate_planned_load
from app.planning.projection import project_load_series, projection_warnings
from app.planning.matching import activity_match_score


def test_planned_threshold_hour_is_near_threshold_load():
    estimate = estimate_planned_load(3600, intensity="threshold")
    assert 85 <= estimate.load <= 100
    assert estimate.intensity_factor == 0.95


def test_future_projection_uses_planned_load_only_after_today():
    today = date.today()
    tomorrow = today + timedelta(days=1)
    rows = project_load_series({today: 80}, {today: 200, tomorrow: 100}, today, tomorrow)
    assert rows[0]["load"] == 80
    assert rows[0]["projected"] is False
    assert rows[1]["load"] == 100
    assert rows[1]["projected"] is True


def test_projection_warns_on_large_weekly_ramp():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    rows=[]
    for i in range(14):
        d=monday+timedelta(days=i)
        rows.append({"date":d.isoformat(),"load":20 if i<7 else 40,"planned_load":0,"actual_load":0})
    warnings=projection_warnings(rows)
    assert any(w["code"] == "weekly_load_spike" for w in warnings)


def test_matching_prefers_same_sport_close_in_time_and_duration():
    now = datetime(2026,8,27,18,0)
    workout = SimpleNamespace(sport="running", scheduled_at=now, duration_s=3600, distance_m=10000)
    activity = SimpleNamespace(sport="running", start_time=now+timedelta(minutes=20), duration_s=3500, distance_m=9800)
    score=activity_match_score(workout, activity)
    assert score.score > 0.9
    assert "same sport" in score.reasons
