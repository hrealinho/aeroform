from datetime import datetime, timezone

from app.importers.common import ParsedActivity
from app.metrics.load import terrain_load_profile, composite_training_load
from app.metrics.terrain import elevation_gain_loss, grade_distribution
from app.services.classification import classify_parsed


def parsed(**overrides):
    base = dict(
        sport="other",
        subtype=None,
        name="test",
        start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        duration_s=3600,
        distance_m=10000,
        elevation_gain_m=0,
        elevation_loss_m=0,
        streams=[],
        source_metadata={"classification_ambiguous": True},
    )
    base.update(overrides)
    return ParsedActivity(**base)


def test_trail_descent_contributes_more_than_ascent():
    p = terrain_load_profile("trail_running", 2 * 3600, 20000, 1000, 1000)
    assert p is not None
    assert p.descent_load > p.ascent_load
    assert p.mechanical_load > p.distance_load


def test_trail_composite_reflects_terrain_beyond_same_metabolic_load():
    flat = terrain_load_profile("running", 2.5 * 3600, 20000, 50, 50)
    trail = terrain_load_profile("trail_running", 2.5 * 3600, 20000, 1500, 1500)
    flat_total, _ = composite_training_load("running", 150, flat)
    trail_total, _ = composite_training_load("trail_running", 150, trail)
    assert trail_total > flat_total


def test_hiking_blend_does_not_blindly_add_long_hr_load_and_mechanical_load():
    terrain = terrain_load_profile("hiking", 5 * 3600, 20000, 1200, 1200)
    total, weights = composite_training_load("hiking", 245, terrain)
    assert terrain is not None
    assert total < 245 + terrain.mechanical_load
    assert weights["metabolic_weight"] < 1
    assert weights["mechanical_weight"] > 0


def test_fit_trail_subsport_wins_over_generic_running_sport():
    a = parsed(
        sport="trail_running",
        subtype="trail",
        source_metadata={"raw_sport": "running", "raw_sub_sport": "trail", "classification_ambiguous": False},
    )
    result = classify_parsed(a)
    assert result.sport == "trail_running"
    assert result.confidence == "high"


def test_ambiguous_gpx_with_high_vertical_density_is_trail_running():
    a = parsed(duration_s=90 * 60, distance_m=12000, elevation_gain_m=900)
    result = classify_parsed(a)
    assert result.sport == "trail_running"
    assert result.confidence == "medium"


def test_ambiguous_slow_trace_is_hiking():
    a = parsed(duration_s=3 * 3600, distance_m=12000, elevation_gain_m=700)
    result = classify_parsed(a)
    assert result.sport == "hiking"


def test_elevation_gain_loss_ignores_tiny_noise():
    samples = [{"altitude": 100 + x} for x in [0, .1, -.1, .2, 0, 5, 10, 5, 0]]
    gain, loss = elevation_gain_loss(samples)
    assert gain is not None and loss is not None
    assert gain > 0
    assert loss > 0
    assert gain < 20


def test_grade_distribution_tracks_steep_downhill():
    samples = [
        {"distance": 0, "altitude": 100},
        {"distance": 100, "altitude": 90},
        {"distance": 200, "altitude": 80},
    ]
    grades = grade_distribution(samples)
    assert grades is not None
    assert grades["steep_down"] == 2
