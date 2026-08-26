from __future__ import annotations
from datetime import datetime, date
from enum import Enum
from sqlalchemy import String, Integer, Float, DateTime, Date, ForeignKey, Text, JSON, UniqueConstraint, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base


class Sport(str, Enum):
    RUNNING = "running"
    TRAIL_RUNNING = "trail_running"
    CYCLING = "cycling"
    HIKING = "hiking"
    MOUNTAINEERING = "mountaineering"
    CLIMBING = "climbing"
    OTHER = "other"


class Athlete(Base):
    __tablename__ = "athletes"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AthleteThreshold(Base):
    __tablename__ = "athlete_thresholds"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    sport: Mapped[str] = mapped_column(String(32), index=True)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[float] = mapped_column(Float)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    confidence: Mapped[str] = mapped_column(String(16), default="high")


class ImportSession(Base):
    __tablename__ = "import_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class RawActivityFile(Base):
    __tablename__ = "raw_activity_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    import_session_id: Mapped[int | None] = mapped_column(ForeignKey("import_sessions.id"), nullable=True)
    filename: Mapped[str] = mapped_column(String(500))
    storage_key: Mapped[str] = mapped_column(String(1000), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    parser_version: Mapped[str] = mapped_column(String(32), default="v0.1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("athlete_id", "fingerprint", name="uq_activity_fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    sport: Mapped[str] = mapped_column(String(32), index=True)
    subtype: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_s: Mapped[float] = mapped_column(Float, default=0)
    moving_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_loss_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_cadence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    sources: Mapped[list[ActivitySource]] = relationship(back_populates="activity", cascade="all, delete-orphan")
    metrics: Mapped[ActivityMetrics | None] = relationship(back_populates="activity", cascade="all, delete-orphan", uselist=False)
    streams: Mapped[ActivityStreams | None] = relationship(back_populates="activity", cascade="all, delete-orphan", uselist=False)


class ActivitySource(Base):
    __tablename__ = "activity_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    raw_file_id: Mapped[int | None] = mapped_column(ForeignKey("raw_activity_files.id"), nullable=True)
    import_session_id: Mapped[int | None] = mapped_column(ForeignKey("import_sessions.id"), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    activity: Mapped[Activity] = relationship(back_populates="sources")


class ActivityStreams(Base):
    __tablename__ = "activity_streams"
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), unique=True)
    samples: Mapped[list] = mapped_column(JSON, default=list)
    activity: Mapped[Activity] = relationship(back_populates="streams")


class ActivityMetrics(Base):
    __tablename__ = "activity_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), unique=True)
    training_load: Mapped[float] = mapped_column(Float, default=0)
    load_method: Mapped[str] = mapped_column(String(64), default="unknown")
    load_confidence: Mapped[str] = mapped_column(String(16), default="low")
    intensity_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    trimp: Mapped[float | None] = mapped_column(Float, nullable=True)
    mechanical_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    metric_version: Mapped[str] = mapped_column(String(32), default="v0.1")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    activity: Mapped[Activity] = relationship(back_populates="metrics")


class Objective(Base):
    __tablename__ = "objectives"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    event_date: Mapped[date] = mapped_column(Date, index=True)
    sport: Mapped[str] = mapped_column(String(32))
    subtype: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[str] = mapped_column(String(1), default="A")
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class TrainingBlock(Base):
    __tablename__ = "training_blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    objective_id: Mapped[int | None] = mapped_column(ForeignKey("objectives.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    block_type: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    targets: Mapped[dict] = mapped_column(JSON, default=dict)


class PlannedWorkout(Base):
    __tablename__ = "planned_workouts"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    block_id: Mapped[int | None] = mapped_column(ForeignKey("training_blocks.id"), nullable=True)
    objective_id: Mapped[int | None] = mapped_column(ForeignKey("objectives.id"), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sport: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    matched_activity_id: Mapped[int | None] = mapped_column(ForeignKey("activities.id"), nullable=True)
