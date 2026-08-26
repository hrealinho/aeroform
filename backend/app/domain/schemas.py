from datetime import datetime, date
from pydantic import BaseModel, ConfigDict, Field


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sport: str
    subtype: str | None = None
    name: str | None = None
    start_time: datetime
    duration_s: float
    distance_m: float | None = None
    elevation_gain_m: float | None = None
    avg_hr: float | None = None
    avg_power: float | None = None
    training_load: float | None = None
    load_method: str | None = None
    load_confidence: str | None = None


class ObjectiveCreate(BaseModel):
    name: str
    event_date: date
    sport: str
    subtype: str | None = None
    priority: str = Field(default="A", pattern="^[ABC]$")
    distance_m: float | None = None
    elevation_m: float | None = None
    expected_duration_s: float | None = None
    target: dict | None = None
    notes: str | None = None


class PlannedWorkoutCreate(BaseModel):
    scheduled_at: datetime
    sport: str
    name: str
    duration_s: float | None = None
    distance_m: float | None = None
    elevation_m: float | None = None
    projected_load: float | None = None
    steps: list[dict] = []
    objective_id: int | None = None
    block_id: int | None = None
    locked: bool = False
