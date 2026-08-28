from datetime import datetime, timezone

from app.importers.common import ParsedActivity
from app.integrations.strava.mapper import map_activity
from app.metrics.load import rpe_load


def test_parsed_activity_supports_rpe_fallback():
    parsed = ParsedActivity(
        sport="running", subtype="Run", name="Easy",
        start_time=datetime(2026, 8, 1, tzinfo=timezone.utc), duration_s=3600, rpe=3.0,
    )
    assert parsed.rpe == 3.0
    assert rpe_load(parsed.duration_s, parsed.rpe).load > 0


def test_strava_perceived_exertion_is_mapped_to_rpe():
    parsed = map_activity({
        "id": 123, "sport_type": "Run", "name": "Run",
        "start_date": "2026-08-01T10:00:00Z", "elapsed_time": 3600,
        "moving_time": 3500, "distance": 10000, "total_elevation_gain": 100,
        "perceived_exertion": 6,
    })
    assert parsed.rpe == 6.0
    # Elevation loss is now estimated rather than left null. Strava's summary payload has
    # no loss field, and leaving it null meant descent load was zero on every
    # summary-imported activity. The estimate is always labelled with its basis.
    assert parsed.elevation_loss_m == 100.0
    assert parsed.source_metadata["elevation_loss_source"] == "estimated_assumed_return"
    assert parsed.source_metadata["elevation_loss_confidence"] == "low"
