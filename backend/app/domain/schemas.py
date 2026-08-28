from datetime import date, datetime
from pydantic import BaseModel, Field, model_validator


class ActivityClassificationUpdate(BaseModel):
    sport: str = Field(pattern="^(running|trail_running|cycling|hiking|mountaineering|climbing|other)$")
    subtype: str | None = None


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


class CoachProfileUpdate(BaseModel):
    available_hours_per_week: float | None = Field(default=None, ge=0, le=80)
    preferred_long_day: int | None = Field(default=None, ge=0, le=6)
    preferred_rest_day: int | None = Field(default=None, ge=0, le=6)
    doubles_allowed: bool = False
    preferences: dict = Field(default_factory=dict)


class CoachAsk(BaseModel):
    question: str = Field(min_length=2, max_length=4000)


class GenerateWeekRequest(BaseModel):
    week_start: date | None = None
    objective_id: int | None = None
    strategy: str = "balanced"


class AdaptWeekRequest(BaseModel):
    week_start: date | None = None


class PlanCommand(BaseModel):
    action: str = Field(pattern="^(create_workout|update_workout|move_workout|delete_workout)$")
    workout_id: int | None = None
    scheduled_at: datetime | None = None
    sport: str | None = None
    name: str | None = None
    duration_s: float | None = Field(default=None, ge=0)
    distance_m: float | None = Field(default=None, ge=0)
    elevation_m: float | None = Field(default=None, ge=0)
    intensity: str | None = None
    steps: list[dict] | None = None
    objective_id: int | None = None
    block_id: int | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.action == "create_workout":
            if not self.scheduled_at or not self.sport or not self.name:
                raise ValueError("create_workout requires scheduled_at, sport, and name")
        else:
            if self.workout_id is None:
                raise ValueError(f"{self.action} requires workout_id")
        if self.action == "move_workout" and self.scheduled_at is None:
            raise ValueError("move_workout requires scheduled_at")
        return self

class ThresholdManualCreate(BaseModel):
    sport: str = Field(pattern="^(global|running|trail_running|cycling|hiking|mountaineering|climbing)$")
    metric: str = Field(pattern="^(ftp|critical_power|threshold_hr|threshold_speed_mps|max_hr|resting_hr)$")
    value: float = Field(gt=0)
    # Omitted valid_from back-dates to the first activity so the threshold actually
    # applies to stored history; pass an explicit date to scope it to a period.
    valid_from: date | None = None
    apply_to_history: bool = True
    recompute: bool = True


class ThresholdEstimateRequest(BaseModel):
    history_days: int = Field(default=365, ge=30, le=3650)
    persist: bool = True
    apply_to_history: bool = False
