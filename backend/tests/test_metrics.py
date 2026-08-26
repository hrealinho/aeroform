from datetime import date
from app.metrics.load import power_load, rpe_load, mountain_mechanical_load
from app.metrics.fitness import ewma_series


def test_one_hour_at_threshold_is_100():
    assert power_load(3600, 250, 250).load == 100.0


def test_rpe_load_is_positive():
    assert rpe_load(3600, 5).load == 60.0


def test_mountain_descent_weights_more_than_gain():
    assert mountain_mechanical_load(3600, 0, 1000) > mountain_mechanical_load(3600, 1000, 0)


def test_fitness_series():
    data = ewma_series({date(2026, 1, 1): 100}, date(2026, 1, 1), date(2026, 1, 2))
    assert len(data) == 2
    assert data[0]["fitness"] > 0
