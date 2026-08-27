from app.metrics.streams import normalized_power, zone_seconds, aerobic_decoupling


def test_normalized_power_constant_power():
    samples = [{"power": 250} for _ in range(120)]
    assert normalized_power(samples) == 250.0


def test_zone_seconds():
    samples = [{"power": 100}, {"power": 200}, {"power": 300}]
    assert zone_seconds(samples, "power", [150, 250]) == {"z1": 1, "z2": 1, "z3": 1}


def test_decoupling_detects_efficiency_drop():
    first = [{"power": 200, "hr": 140} for _ in range(120)]
    second = [{"power": 180, "hr": 145} for _ in range(120)]
    drift = aerobic_decoupling(first + second)
    assert drift is not None and drift > 0
