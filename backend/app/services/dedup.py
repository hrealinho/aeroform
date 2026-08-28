import hashlib
from datetime import datetime

from app.core.timeutil import to_utc


def activity_fingerprint(sport: str, start_time: datetime, duration_s: float, distance_m: float | None, external_hint: str | None = None) -> str:
    # Normalize to UTC first: the same instant arriving as "+02:00" from a GPX file,
    # as "Z" from Strava, or naive after a SQLite round-trip must hash identically or
    # the same activity is stored several times.
    start_bucket = to_utc(start_time).replace(second=0, microsecond=0).isoformat()
    duration_bucket = round(duration_s / 10) * 10
    distance_bucket = None if distance_m is None else round(distance_m / 20) * 20
    raw = f"{sport}|{start_bucket}|{duration_bucket}|{distance_bucket}|{external_hint or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()
