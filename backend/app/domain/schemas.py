from datetime import datetime, date, time
from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class TrainingBlockCreate(BaseModel):
    name: str
    block_type: str = "base"
    start_date: date
    end_date: date
    objective_id: int | None = None
    targets: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class TrainingBlockUpdate(BaseModel):
    name: str | None = None
    block_type: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    objective_id: int | None = None
    targets: dict | None = None


class PlannedWorkoutCreate(BaseModel):
    scheduled_at: datetime
    sport: str
    name: str
    duration_s: float | None = None
    distance_m: float | None = None
    elevation_m: float | None = None
    projected_load: float | None = None
    steps: list[dict] = Field(default_factory=list)
    objective_id: int | None = None
    block_id: int | None = None
    locked: bool = False
    intensity: str | None = None


class PlannedWorkoutUpdate(BaseModel):
    scheduled_at: datetime | None = None
    sport: str | None = None
    name: str | None = None
    duration_s: float | None = None
    distance_m: float | None = None
    elevation_m: float | None = None
    projected_load: float | None = None
    steps: list[dict] | None = None
    objective_id: int | None = None
    block_id: int | None = None
    locked: bool | None = None
    intensity: str | None = None


class ManualMatch(BaseModel):
    activity_id: int | None = None


class PlanningConstraintCreate(BaseModel):
    constraint_type: str
    start_date: date
    end_date: date | None = None
    weekdays: list[int] = Field(default_factory=list)
    value: dict = Field(default_factory=dict)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("weekdays must use Monday=0 through Sunday=6")
        return self
