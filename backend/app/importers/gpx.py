from datetime import datetime
from math import radians, sin, cos, sqrt, atan2
import xml.etree.ElementTree as ET
from app.importers.common import ParsedActivity


def haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2-lat1)
    dl = radians(lon2-lon1)
    a = sin(dphi/2)**2 + cos(p1)*cos(p2)*sin(dl/2)**2
    return 2*r*atan2(sqrt(a), sqrt(1-a))


def parse_gpx(path: str) -> ParsedActivity:
    root = ET.parse(path).getroot()
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    pts = root.findall(".//g:trkpt", ns) or root.findall(".//{*}trkpt")
    streams = []
    distance = gain = loss = 0.0
    last = None
    times = []
    for p in pts:
        lat, lon = float(p.attrib["lat"]), float(p.attrib["lon"])
        ele_el = p.find("{*}ele")
        time_el = p.find("{*}time")
        ele = float(ele_el.text) if ele_el is not None and ele_el.text else None
        t = datetime.fromisoformat(time_el.text.replace("Z", "+00:00")) if time_el is not None and time_el.text else None
        sample = {"lat": lat, "lon": lon, "altitude": ele, "time": t.isoformat() if t else None}
        streams.append(sample)
        if t: times.append(t)
        if last:
            distance += haversine(last[0], last[1], lat, lon)
            if ele is not None and last[2] is not None:
                delta = ele-last[2]
                if delta > 0: gain += delta
                else: loss += -delta
        last = (lat, lon, ele)
    if not times:
        raise ValueError("GPX contains no timestamped track points")
    return ParsedActivity("running", None, "GPX activity", times[0], max((times[-1]-times[0]).total_seconds(), 0), distance_m=distance, elevation_gain_m=gain, elevation_loss_m=loss, streams=streams)
