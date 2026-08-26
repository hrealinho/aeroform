from datetime import datetime, timezone
from fitparse import FitFile
from app.importers.common import ParsedActivity


def parse_fit(path: str) -> ParsedActivity:
    fit = FitFile(path)
    session_values = {}
    for msg in fit.get_messages("session"):
        session_values.update({f.name: f.value for f in msg})
        break
    sport_map = {"running": "running", "cycling": "cycling", "hiking": "hiking", "walking": "hiking", "mountaineering": "mountaineering"}
    sport = sport_map.get(str(session_values.get("sport", "other")).lower(), "other")
    streams = []
    power_values = []
    hr_values = []
    for msg in fit.get_messages("record"):
        values = {f.name: f.value for f in msg}
        t = values.get("timestamp")
        if isinstance(t, datetime) and t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        p = values.get("power")
        hr = values.get("heart_rate")
        if isinstance(p, (int, float)): power_values.append(float(p))
        if isinstance(hr, (int, float)): hr_values.append(float(hr))
        streams.append({"time": t.isoformat() if isinstance(t, datetime) else None, "lat": values.get("position_lat"), "lon": values.get("position_long"), "altitude": values.get("altitude"), "distance": values.get("distance"), "hr": hr, "power": p, "cadence": values.get("cadence"), "speed": values.get("enhanced_speed") or values.get("speed")})
    start = session_values.get("start_time") or session_values.get("timestamp")
    if not isinstance(start, datetime):
        starts = [s["time"] for s in streams if s.get("time")]
        if not starts: raise ValueError("FIT contains no start time")
        start = datetime.fromisoformat(starts[0])
    if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
    duration = float(session_values.get("total_timer_time") or session_values.get("total_elapsed_time") or 0)
    distance = session_values.get("total_distance")
    ascent = session_values.get("total_ascent")
    descent = session_values.get("total_descent")
    avg_power = session_values.get("avg_power") or (sum(power_values)/len(power_values) if power_values else None)
    avg_hr = session_values.get("avg_heart_rate") or (sum(hr_values)/len(hr_values) if hr_values else None)
    return ParsedActivity(sport, str(session_values.get("sub_sport")) if session_values.get("sub_sport") else None, "FIT activity", start, duration, distance_m=float(distance) if distance is not None else None, elevation_gain_m=float(ascent) if ascent is not None else None, elevation_loss_m=float(descent) if descent is not None else None, avg_hr=float(avg_hr) if avg_hr is not None else None, max_hr=float(session_values.get("max_heart_rate")) if session_values.get("max_heart_rate") else (max(hr_values) if hr_values else None), avg_power=float(avg_power) if avg_power is not None else None, normalized_power=float(session_values.get("normalized_power")) if session_values.get("normalized_power") else None, avg_cadence=float(session_values.get("avg_cadence")) if session_values.get("avg_cadence") else None, streams=streams, source_metadata={"manufacturer": str(session_values.get("manufacturer", ""))})
