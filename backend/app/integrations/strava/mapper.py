from __future__ import annotations
from datetime import datetime, timezone, timedelta
from math import atan2, cos, radians, sin, sqrt

from app.importers.common import ParsedActivity, ParsedLap
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


# Strava's summary payload carries total_elevation_gain but no elevation loss, and loss
# is only derivable from streams. With STRAVA_SYNC_STREAMS off (the default) that left
# elevation_loss null on every activity, so descent_load was 0 across an entire history
# and v0.5's descent weighting contributed nothing at all.
#
# Most training routes finish where they started, so for a closed loop loss == gain is a
# good approximation rather than a guess. Openness is measured from the summary's own
# start/end coordinates. Either way the value is labelled estimated, with its basis
# recorded, so it is never confused with a device-reported total.
LOOP_CLOSURE_TOLERANCE_M = 750.0


def _haversine_m(a: list, b: list) -> float | None:
    if not (isinstance(a, list) and isinstance(b, list) and len(a) == 2 and len(b) == 2):
        return None
    radius = 6371000.0
    lat1, lon1, lat2, lon2 = float(a[0]), float(a[1]), float(b[0]), float(b[1])
    p1, p2 = radians(lat1), radians(lat2)
    dphi, dlambda = radians(lat2 - lat1), radians(lon2 - lon1)
    h = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlambda / 2) ** 2
    return 2 * radius * atan2(sqrt(h), sqrt(1 - h))


def estimate_elevation_loss(data: dict, gain: float | None) -> tuple[float | None, dict]:
    """Approximate elevation loss for a summary-only activity.

    Returns (loss, provenance). Provenance is stored in source metadata so the UI and
    the coach can tell an estimate from a measurement.
    """
    if gain is None or gain <= 0:
        return None, {"elevation_loss_source": "unavailable"}
    closure = _haversine_m(data.get("start_latlng"), data.get("end_latlng"))
    if closure is not None and closure <= LOOP_CLOSURE_TOLERANCE_M:
        return gain, {
            "elevation_loss_source": "estimated_closed_loop",
            "elevation_loss_confidence": "medium",
            "route_closure_m": round(closure),
        }
    if closure is None:
        return gain, {
            "elevation_loss_source": "estimated_assumed_return",
            "elevation_loss_confidence": "low",
            "route_closure_m": None,
        }
    # Point to point: the net descent is unknown, so assume the route gave back what it
    # climbed minus the net displacement it could not account for. Flagged low.
    return gain, {
        "elevation_loss_source": "estimated_open_route",
        "elevation_loss_confidence": "low",
        "route_closure_m": round(closure),
    }


def map_laps(laps: list[dict] | None) -> list[ParsedLap]:
    """Map Strava's /activities/{id}/laps payload.

    Only available on the detailed endpoint, so a summary-only backfill has none.
    """
    if not isinstance(laps, list):
        return []
    out: list[ParsedLap] = []
    for index, lap in enumerate(sorted(laps, key=lambda l: l.get("lap_index") or 0)):
        if not isinstance(lap, dict):
            continue
        elapsed = _f(lap.get("elapsed_time"))
        distance = _f(lap.get("distance"))
        out.append(ParsedLap(
            index=index,
            name=lap.get("name"),
            start_time=_dt(lap["start_date"]) if lap.get("start_date") else None,
            elapsed_s=elapsed,
            moving_s=_f(lap.get("moving_time")),
            distance_m=distance,
            elevation_gain_m=_f(lap.get("total_elevation_gain")),
            avg_hr=_f(lap.get("average_heartrate")),
            max_hr=_f(lap.get("max_heartrate")),
            avg_power=_f(lap.get("average_watts")),
            avg_cadence=_f(lap.get("average_cadence")),
            avg_speed_mps=_f(lap.get("average_speed")),
            max_speed_mps=_f(lap.get("max_speed")),
        ))
    return out


def _f(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def map_activity(data: dict, streams: dict[str, list] | None = None, laps: list[dict] | None = None) -> ParsedActivity:
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

    gain = float(data["total_elevation_gain"]) if data.get("total_elevation_gain") is not None else terrain.get("stream_elevation_gain_m")
    # Streams win when present; otherwise fall back to an explicitly labelled estimate.
    loss = terrain.get("stream_elevation_loss_m")
    loss_provenance = {"elevation_loss_source": "stream"} if loss is not None else {}
    if loss is None:
        loss, loss_provenance = estimate_elevation_loss(data, gain)

    return ParsedActivity(
        sport=sport,
        subtype=sport_type,
        name=data.get("name") or "Strava activity",
        start_time=_dt(data["start_date"]),
        duration_s=float(data.get("elapsed_time") or data.get("moving_time") or 0),
        moving_time_s=float(data["moving_time"]) if data.get("moving_time") is not None else None,
        distance_m=float(data["distance"]) if data.get("distance") is not None else None,
        elevation_gain_m=gain,
        elevation_loss_m=loss,
        avg_hr=float(data["average_heartrate"]) if data.get("average_heartrate") is not None else None,
        max_hr=float(data["max_heartrate"]) if data.get("max_heartrate") is not None else None,
        avg_power=float(data["average_watts"]) if data.get("average_watts") is not None else None,
        normalized_power=float(data["weighted_average_watts"]) if data.get("weighted_average_watts") is not None else None,
        avg_cadence=float(data["average_cadence"]) if data.get("average_cadence") is not None else None,
        rpe=float(data["perceived_exertion"]) if data.get("perceived_exertion") is not None else None,
        streams=stream_samples,
        laps=map_laps(laps),
        source_metadata={
            "strava_id": str(data.get("id")),
            "device_name": data.get("device_name"),
            "trainer": data.get("trainer"),
            "commute": data.get("commute"),
            "private": data.get("private"),
            "sport_type": sport_type,
            "classification_ambiguous": sport == "other",
            **loss_provenance,
        },
    )
