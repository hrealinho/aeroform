from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ParsedActivity:
    sport: str
    subtype: str | None
    name: str | None
    start_time: datetime
    duration_s: float
    moving_time_s: float | None = None
    distance_m: float | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_power: float | None = None
    normalized_power: float | None = None
    avg_cadence: float | None = None
    rpe: float | None = None
    streams: list[dict] = field(default_factory=list)
    source_metadata: dict = field(default_factory=dict)
