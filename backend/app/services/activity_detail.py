"""Full detail payload for a single activity.

Streams are downsampled before they leave the API. A three-hour ride at 1 Hz is
~11k samples per channel; sending that to a chart is wasteful and the chart cannot
draw it at pixel resolution anyway. Downsampling is done by bucketed averaging so
the shape survives, with min/max kept for altitude so ridgelines are not flattened.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import to_utc
from app.domain.models import Activity, ActivityLap, ActivityMetrics, ActivitySource
from app.metrics.terrain import sample_durations

MAX_CHART_POINTS = 600
CHANNELS = ("power", "hr", "cadence", "speed", "altitude", "grade", "temperature")


def _num(value):
    return float(value) if isinstance(value, (int, float)) else None


def downsample(samples: list[dict], max_points: int = MAX_CHART_POINTS) -> list[dict]:
    """Bucket-average a stream down to at most ``max_points`` rows."""
    if not samples:
        return []
    durations = sample_durations(samples)
    bucket_count = min(max_points, len(samples))
    step = len(samples) / bucket_count

    out: list[dict] = []
    elapsed = 0.0
    for index in range(bucket_count):
        start = int(index * step)
        end = max(start + 1, int((index + 1) * step))
        chunk = samples[start:end]
        row: dict = {"t": round(elapsed)}
        elapsed += sum(durations[start:end])
        for channel in CHANNELS:
            values = [v for v in (_num(s.get(channel)) for s in chunk) if v is not None]
            if values:
                row[channel] = round(sum(values) / len(values), 2)
        # Altitude keeps its extremes: averaging alone flattens a ridgeline.
        altitudes = [v for v in (_num(s.get("altitude")) for s in chunk) if v is not None]
        if altitudes:
            row["altitude_min"] = round(min(altitudes), 1)
            row["altitude_max"] = round(max(altitudes), 1)
        distances = [v for v in (_num(s.get("distance")) for s in chunk) if v is not None]
        if distances:
            row["distance"] = round(max(distances), 1)
        out.append(row)
    return out


def _lap_dict(lap: ActivityLap) -> dict:
    pace = None
    if lap.distance_m and lap.moving_s and lap.distance_m > 0 and lap.moving_s > 0:
        per_km = lap.moving_s / (lap.distance_m / 1000.0)
        pace = f"{int(per_km // 60)}:{int(round(per_km % 60)):02d}/km"
    return {
        "index": lap.lap_index, "name": lap.name,
        "start_time": to_utc(lap.start_time).isoformat() if lap.start_time else None,
        "elapsed_s": lap.elapsed_s, "moving_s": lap.moving_s, "distance_m": lap.distance_m,
        "elevation_gain_m": lap.elevation_gain_m, "elevation_loss_m": lap.elevation_loss_m,
        "avg_hr": lap.avg_hr, "max_hr": lap.max_hr,
        "avg_power": lap.avg_power, "max_power": lap.max_power, "normalized_power": lap.normalized_power,
        "avg_cadence": lap.avg_cadence, "avg_speed_mps": lap.avg_speed_mps, "max_speed_mps": lap.max_speed_mps,
        "pace": pace, "trigger": lap.trigger,
    }


def _zone_list(raw, label_prefix: str) -> list[dict] | None:
    """Turn a {z1: seconds} map into an ordered list a chart can consume."""
    if not isinstance(raw, dict) or not raw:
        return None
    items = []
    for key in sorted(raw, key=lambda k: int(str(k).lstrip("z") or 0)):
        seconds = raw.get(key)
        if isinstance(seconds, (int, float)):
            items.append({"zone": f"{label_prefix}{str(key).lstrip('z')}", "seconds": round(float(seconds), 1)})
    total = sum(i["seconds"] for i in items)
    for item in items:
        item["pct"] = round(item["seconds"] / total * 100, 1) if total > 0 else 0.0
    return items or None


def activity_detail(db: Session, athlete_id: int, activity_id: int) -> dict | None:
    activity = db.get(Activity, activity_id)
    if not activity or activity.athlete_id != athlete_id:
        return None
    metrics: ActivityMetrics | None = activity.metrics
    details = metrics.details if metrics and isinstance(metrics.details, dict) else {}
    terrain = details.get("terrain") if isinstance(details.get("terrain"), dict) else {}
    samples = activity.streams.samples if activity.streams and activity.streams.samples else []
    sources = list(db.scalars(select(ActivitySource).where(ActivitySource.activity_id == activity.id)))

    return {
        "id": activity.id,
        "sport": activity.sport,
        "subtype": activity.subtype,
        "name": activity.name,
        "start_time": to_utc(activity.start_time).isoformat(),
        "summary": {
            "duration_s": activity.duration_s, "moving_time_s": activity.moving_time_s,
            "distance_m": activity.distance_m,
            "elevation_gain_m": activity.elevation_gain_m, "elevation_loss_m": activity.elevation_loss_m,
            "avg_hr": activity.avg_hr, "max_hr": activity.max_hr,
            "avg_power": activity.avg_power, "normalized_power": activity.normalized_power,
            "avg_cadence": activity.avg_cadence, "rpe": activity.rpe,
            "avg_speed_mps": (activity.distance_m / activity.moving_time_s)
                if activity.distance_m and activity.moving_time_s else None,
        },
        "load": {
            "training_load": metrics.training_load if metrics else None,
            "metabolic_load": details.get("metabolic_load"),
            "mechanical_load": metrics.mechanical_load if metrics else None,
            "method": metrics.load_method if metrics else None,
            "confidence": metrics.load_confidence if metrics else None,
            "intensity_factor": metrics.intensity_factor if metrics else None,
            "trimp": metrics.trimp if metrics else None,
            "metric_version": metrics.metric_version if metrics else None,
            "composite": details.get("composite"),
            "durations": details.get("durations"),
        },
        "terrain": terrain or None,
        "classification": details.get("classification"),
        "streams": {
            "points": downsample(samples),
            "sample_count": len(samples),
            "channels": sorted({c for c in CHANNELS if any(s.get(c) is not None for s in samples[:200])}),
        },
        "zones": {
            "hr": _zone_list(details.get("hr_zones_s"), "Z"),
            "power": _zone_list(details.get("power_zones_s"), "Z"),
        },
        "grade_distribution_s": details.get("grade_distribution_s"),
        "aerobic_decoupling_pct": details.get("aerobic_decoupling_pct"),
        "mean_max_power": details.get("mean_max_power"),
        "laps": [_lap_dict(lap) for lap in (activity.laps or [])],
        "sources": [{
            "type": s.source_type, "external_id": s.external_id,
            "original_filename": (s.metadata_json or {}).get("original_filename"),
            "elevation_loss_source": (s.metadata_json or {}).get("elevation_loss_source"),
        } for s in sources],
    }
