from __future__ import annotations

from dataclasses import dataclass

from app.importers.common import ParsedActivity
from app.metrics.terrain import moving_time_from_streams


@dataclass(frozen=True)
class Classification:
    sport: str
    subtype: str | None
    confidence: str
    reason: str


_CANONICAL = {"running", "trail_running", "cycling", "hiking", "mountaineering", "climbing", "other"}
_TRAIL_HINTS = {"trail", "trail_running", "trailrun", "mountain_running", "ultra", "cross_country"}
_HIKE_HINTS = {"hike", "hiking", "walking", "walk", "snowshoe"}
_CYCLE_HINTS = {"cycling", "biking", "ride", "road", "mountain_biking", "gravel", "indoor_cycling"}
_RUN_HINTS = {"running", "run", "road_running", "street", "track", "treadmill"}
_MOUNTAIN_HINTS = {"mountaineering", "alpine", "backcountry", "ski_mountaineering"}


def _hint(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _explicit_from_hint(raw: str | None) -> str | None:
    value = _hint(raw)
    if not value:
        return None
    if value in _TRAIL_HINTS or "trail" in value:
        return "trail_running"
    if value in _HIKE_HINTS or "hik" in value or value == "walking":
        return "hiking"
    if value in _MOUNTAIN_HINTS or "mountaineer" in value:
        return "mountaineering"
    if value in _CYCLE_HINTS or "cycl" in value or "bike" in value or "ride" in value:
        return "cycling"
    if value in _RUN_HINTS or value.startswith("run"):
        return "running"
    if "climb" in value or "boulder" in value:
        return "climbing"
    return None


def classify_parsed(parsed: ParsedActivity) -> Classification:
    """Normalize source sport metadata and conservatively classify ambiguous files.

    Explicit Strava/FIT/TCX sport metadata always wins. Heuristics are used mainly
    for GPX files that do not declare a sport and are therefore intentionally marked
    medium/low confidence so the athlete can override them.
    """
    metadata = parsed.source_metadata or {}
    explicit_values = [
        metadata.get("sport_type"), metadata.get("raw_sub_sport"), metadata.get("raw_sport"),
        metadata.get("declared_type"), parsed.subtype,
    ]
    for raw in explicit_values:
        explicit = _explicit_from_hint(raw)
        if explicit:
            subtype = parsed.subtype or (str(raw) if raw else None)
            return Classification(explicit, subtype, "high", f"source declared {raw}")

    # Keep already canonical, non-generic parser mappings unless the source marked
    # them as ambiguous. This prevents a hilly road run being relabelled as trail.
    ambiguous = bool(metadata.get("classification_ambiguous"))
    if parsed.sport in _CANONICAL - {"other"} and not ambiguous:
        return Classification(parsed.sport, parsed.subtype, "high", "canonical source sport")

    # Speed must be based on moving time. GPX never declares it, so it is derived from
    # the stream when available; using elapsed time made any run with a long stop look
    # slow enough to be classified as a hike, which then changed its load weighting.
    moving = parsed.moving_time_s or moving_time_from_streams(parsed.streams or [])
    duration = max(float(moving or parsed.duration_s or 0), 1.0)
    distance = max(float(parsed.distance_m or 0), 0.0)
    gain = max(float(parsed.elevation_gain_m or 0), 0.0)
    km = distance / 1000.0
    speed_m_s = distance / duration if distance else 0.0
    gain_per_km = gain / km if km > 0 else 0.0
    basis = "moving time" if moving else "elapsed time"

    # Conservative GPX-style heuristics. Speed separates bike/hike reasonably well;
    # elevation density only distinguishes trail from road once the trace already
    # looks like running.
    if speed_m_s >= 5.5:
        return Classification("cycling", parsed.subtype, "medium", f"ambiguous file, average speed {speed_m_s:.1f} m/s ({basis})")
    if speed_m_s and speed_m_s <= 2.15:
        return Classification("hiking", parsed.subtype, "medium", f"ambiguous file, average speed {speed_m_s:.1f} m/s ({basis})")
    if km >= 3 and gain_per_km >= 30:
        return Classification("trail_running", parsed.subtype, "medium", f"ambiguous run-like file, {gain_per_km:.0f} m gain/km")
    if distance > 0:
        return Classification("running", parsed.subtype, "low", "ambiguous file with run-like speed/profile")
    return Classification("other", parsed.subtype, "low", "insufficient data to infer sport")


def apply_classification(parsed: ParsedActivity) -> Classification:
    result = classify_parsed(parsed)
    parsed.sport = result.sport
    parsed.subtype = result.subtype
    parsed.source_metadata = {
        **(parsed.source_metadata or {}),
        "classification": {
            "sport": result.sport,
            "confidence": result.confidence,
            "reason": result.reason,
            "source": "automatic",
        },
    }
    return result
