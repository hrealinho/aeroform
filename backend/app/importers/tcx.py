from datetime import datetime
import xml.etree.ElementTree as ET
from app.importers.common import ParsedActivity


def parse_tcx(path: str) -> ParsedActivity:
    root = ET.parse(path).getroot()
    activity = root.find(".//{*}Activity")
    if activity is None:
        raise ValueError("TCX contains no activity")
    sport_map = {"Running": "running", "Biking": "cycling"}
    sport = sport_map.get(activity.attrib.get("Sport"), "other")
    id_el = activity.find("{*}Id")
    start = datetime.fromisoformat(id_el.text.replace("Z", "+00:00")) if id_el is not None and id_el.text else None
    laps = activity.findall("{*}Lap")
    duration = distance = 0.0
    streams = []
    hrs = []
    for lap in laps:
        duration += float((lap.findtext("{*}TotalTimeSeconds") or 0))
        distance += float((lap.findtext("{*}DistanceMeters") or 0))
        for tp in lap.findall(".//{*}Trackpoint"):
            time_text = tp.findtext("{*}Time")
            hr_text = tp.findtext("{*}HeartRateBpm/{*}Value")
            alt_text = tp.findtext("{*}AltitudeMeters")
            dist_text = tp.findtext("{*}DistanceMeters")
            hr = float(hr_text) if hr_text else None
            if hr: hrs.append(hr)
            streams.append({"time": time_text, "hr": hr, "altitude": float(alt_text) if alt_text else None, "distance": float(dist_text) if dist_text else None})
    if start is None:
        raise ValueError("TCX activity has no start time")
    return ParsedActivity(sport, None, "TCX activity", start, duration, distance_m=distance or None, avg_hr=(sum(hrs)/len(hrs) if hrs else None), max_hr=(max(hrs) if hrs else None), streams=streams)
