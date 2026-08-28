from __future__ import annotations
from datetime import datetime, timezone, timedelta
from app.importers.common import ParsedActivity
from app.metrics.terrain import terrain_stream_metrics


SPORT_MAP = {
    "Run": "running",
    "TrailRun": "trail_running",
    "VirtualRun": "running",
    "Ride": "cycling",
    "MountainBikeRide": "cycling",
    "GravelRide": "cycling",
    "VirtualRide": "cycling",
    "EBikeRide": "cycling",
    "Hike": "hiking",
    "Walk": "hiking",
    "RockClimbing": "climbing",
    "AlpineSki": "mountaineering",
    "BackcountrySki": "mountaineering",
    "Snowshoe": "hiking",
}


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def map_activity(data: dict, streams: dict[str, list] | None = None) -> ParsedActivity:
    sport_type = data.get("sport_type") or data.get("type") or "Other"
    sport = SPORT_MAP.get(sport_type, "other")
    stream_samples: list[dict] = []
    if streams:
        # Strava stream responses are keyed by stream type when key_by_type=true.
        keys = ["time", "latlng", "altitude", "distance", "heartrate", "watts", "cadence", "velocity_smooth", "grade_smooth", "temp"]
        length = max((len((streams.get(k) or {}).get("data", [])) for k in keys), default=0)
        started = _dt(data["start_date"])
        for idx in range(length):
            def value(key):
                vals = (streams.get(key) or {}).get("data", [])
                return vals[idx] if idx < len(vals) else None
            seconds = value("time")
            latlng = value("latlng")
            stream_samples.append({
                "time": (started + timedelta(seconds=float(seconds))).isoformat() if seconds is not None else None,
                "lat": latlng[0] if isinstance(latlng, list) and len(latlng) == 2 else None,
                "lon": latlng[1] if isinstance(latlng, list) and len(latlng) == 2 else None,
                "altitude": value("altitude"),
                "distance": value("distance"),
                "hr": value("heartrate"),
                "power": value("watts"),
                "cadence": value("cadence"),
                "speed": value("velocity_smooth"),
                "grade": value("grade_smooth"),
                "temperature": value("temp"),
            })
    terrain = terrain_stream_metrics(stream_samples, float(data.get("elapsed_time") or data.get("moving_time") or 0)) if stream_samples else {}
    return ParsedActivity(
        sport=sport,
        subtype=sport_type,
        name=data.get("name") or "Strava activity",
        start_time=_dt(data["start_date"]),
        duration_s=float(data.get("elapsed_time") or data.get("moving_time") or 0),
        moving_time_s=float(data["moving_time"]) if data.get("moving_time") is not None else None,
        distance_m=float(data["distance"]) if data.get("distance") is not None else None,
        elevation_gain_m=float(data["total_elevation_gain"]) if data.get("total_elevation_gain") is not None else terrain.get("stream_elevation_gain_m"),
        elevation_loss_m=terrain.get("stream_elevation_loss_m"),
        avg_hr=float(data["average_heartrate"]) if data.get("average_heartrate") is not None else None,
        max_hr=float(data["max_heartrate"]) if data.get("max_heartrate") is not None else None,
        avg_power=float(data["average_watts"]) if data.get("average_watts") is not None else None,
        normalized_power=float(data["weighted_average_watts"]) if data.get("weighted_average_watts") is not None else None,
        avg_cadence=float(data["average_cadence"]) if data.get("average_cadence") is not None else None,
        streams=stream_samples,
        source_metadata={
            "strava_id": str(data.get("id")),
            "device_name": data.get("device_name"),
            "trainer": data.get("trainer"),
            "commute": data.get("commute"),
            "private": data.get("private"),
            "sport_type": sport_type,
            "classification_ambiguous": sport == "other",
        },
    )
