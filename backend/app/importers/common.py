from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ParsedLap:
    """One lap or split as the source recorded it.

    Sources disagree on what a lap is - a button press, an auto-lap every kilometre,
    or a structured workout step - so `trigger` is carried through rather than
    normalised away.
    """

    index: int
    start_time: datetime | None = None
    elapsed_s: float | None = None
    moving_s: float | None = None
    distance_m: float | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_power: float | None = None
    max_power: float | None = None
    normalized_power: float | None = None
    avg_cadence: float | None = None
    avg_speed_mps: float | None = None
    max_speed_mps: float | None = None
    trigger: str | None = None
    name: str | None = None


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
    laps: list[ParsedLap] = field(default_factory=list)
    source_metadata: dict = field(default_factory=dict)
