from app.metrics.thresholds import rolling_best_average, power_zones, hr_zones, pace_zones


def test_rolling_best_average_power():
    samples = [{"power": 200.0}] * 1200 + [{"power": 300.0}] * 1200
    assert rolling_best_average(samples, "power", 1200) == 300.0


def test_cycling_power_zones_use_ftp():
    zones = power_zones(250, "cycling")
    assert len(zones) == 7
    assert zones[1]["min"] == 137.5
    assert zones[3]["max"] == 262.5


def test_lthr_zones():
    zones = hr_zones(170)
    assert len(zones) == 7
    assert zones[3]["min"] == 161.5
    assert zones[4]["min"] == 170.0


def test_pace_zones_have_readable_pace():
    zones = pace_zones(1000 / 240)  # 4:00/km threshold
    assert zones[3]["slower_than"] is not None
    assert zones[3]["faster_than"] is not None
