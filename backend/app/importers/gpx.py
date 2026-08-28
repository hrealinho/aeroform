from datetime import datetime
from math import radians, sin, cos, sqrt, atan2
import xml.etree.ElementTree as ET
from app.importers.common import ParsedActivity
from app.metrics.terrain import elevation_gain_loss


def haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _extension_number(point: ET.Element, names: set[str]) -> float | None:
    for node in point.iter():
        if _local_name(node.tag) in names and node.text:
            try:
                return float(node.text)
            except ValueError:
                pass
    return None


def _declared_type(root: ET.Element) -> str | None:
    for node in root.iter():
        if _local_name(node.tag) == "type" and node.text:
            return node.text.strip()
    return None


def _sport_from_type(value: str | None) -> str:
    raw = (value or "").lower()
    if "trail" in raw:
        return "trail_running"
    if "run" in raw:
        return "running"
    if "bike" in raw or "cycl" in raw or "ride" in raw:
        return "cycling"
    if "hik" in raw or "walk" in raw:
        return "hiking"
    return "other"


def parse_gpx(path: str) -> ParsedActivity:
    root = ET.parse(path).getroot()
    pts = root.findall(".//{*}trkpt")
    streams = []
    distance = 0.0
    last = None
    times = []
    hrs = []
    cadences = []

    for p in pts:
        lat, lon = float(p.attrib["lat"]), float(p.attrib["lon"])
        ele_el = p.find("{*}ele")
        time_el = p.find("{*}time")
        ele = float(ele_el.text) if ele_el is not None and ele_el.text else None
        t = datetime.fromisoformat(time_el.text.replace("Z", "+00:00")) if time_el is not None and time_el.text else None
        hr = _extension_number(p, {"hr", "heartrate"})
        cadence = _extension_number(p, {"cad", "cadence"})
        temperature = _extension_number(p, {"atemp", "temp", "temperature"})
        if hr is not None:
            hrs.append(hr)
        if cadence is not None:
            cadences.append(cadence)

        segment_distance = 0.0
        speed = None
        if last:
            segment_distance = haversine(last[0], last[1], lat, lon)
            distance += segment_distance
            if t and last[3]:
                dt = (t - last[3]).total_seconds()
                if dt > 0:
                    speed = segment_distance / dt
        sample = {
            "lat": lat,
            "lon": lon,
            "altitude": ele,
            "time": t.isoformat() if t else None,
            "distance": distance,
            "speed": speed,
            "hr": hr,
            "cadence": cadence,
            "temperature": temperature,
        }
        streams.append(sample)
        if t:
            times.append(t)
        last = (lat, lon, ele, t)

    if not times:
        raise ValueError("GPX contains no timestamped track points")
    gain, loss = elevation_gain_loss(streams)
    declared = _declared_type(root)
    sport = _sport_from_type(declared)
    duration = max((times[-1] - times[0]).total_seconds(), 0)
    return ParsedActivity(
        sport,
        declared,
        "GPX activity",
        times[0],
        duration,
        distance_m=distance,
        elevation_gain_m=gain,
        elevation_loss_m=loss,
        avg_hr=(sum(hrs) / len(hrs) if hrs else None),
        max_hr=(max(hrs) if hrs else None),
        avg_cadence=(sum(cadences) / len(cadences) if cadences else None),
        streams=streams,
        source_metadata={
            "declared_type": declared,
            "classification_ambiguous": declared is None or sport == "other",
        },
    )
