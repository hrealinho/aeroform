from datetime import date, datetime, timedelta
from pathlib import Path
import os, tempfile
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from app.db.session import get_db
from app.domain.models import Athlete, Activity, ActivityMetrics, ImportSession, Objective, PlannedWorkout
from app.domain.schemas import ObjectiveCreate, PlannedWorkoutCreate
from app.services.ingestion import ingest_file, ingest_zip
from app.metrics.fitness import ewma_series

router = APIRouter(prefix="/api/v1")


def demo_athlete(db: Session):
    athlete = db.scalar(select(Athlete).where(Athlete.email == "demo@endurance.ai"))
    if athlete is None:
        athlete = Athlete(email="demo@endurance.ai", display_name="Demo Athlete", timezone="Europe/Madrid")
        db.add(athlete); db.commit(); db.refresh(athlete)
    return athlete

@router.get("/health")
def health(): return {"status": "ok"}

@router.get("/activities")
def activities(db: Session = Depends(get_db), limit: int = Query(100, ge=1, le=1000), sport: str | None = None):
    athlete = demo_athlete(db)
    stmt = select(Activity, ActivityMetrics).outerjoin(ActivityMetrics).where(Activity.athlete_id == athlete.id).order_by(Activity.start_time.desc()).limit(limit)
    if sport: stmt = stmt.where(Activity.sport == sport)
    rows = db.execute(stmt).all()
    return [{"id": a.id, "sport": a.sport, "subtype": a.subtype, "name": a.name, "start_time": a.start_time, "duration_s": a.duration_s, "distance_m": a.distance_m, "elevation_gain_m": a.elevation_gain_m, "avg_hr": a.avg_hr, "avg_power": a.avg_power, "training_load": m.training_load if m else None, "load_method": m.load_method if m else None, "load_confidence": m.load_confidence if m else None} for a, m in rows]

@router.post("/imports/files")
async def upload_files(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    session = ImportSession(athlete_id=athlete.id, source="upload", status="processing", discovered_count=len(files))
    db.add(session); db.commit(); db.refresh(session)
    errors = []
    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await upload.read()); path = tmp.name
        try:
            if suffix == ".zip": ingest_zip(db, athlete.id, path, session)
            else:
                _, dup = ingest_file(db, athlete.id, path, "upload", session.id)
                if dup: session.duplicate_count += 1
                else: session.imported_count += 1
        except Exception as exc:
            session.failed_count += 1; errors.append({"file": upload.filename, "error": str(exc)})
        finally:
            os.unlink(path); db.commit()
    if session.status == "processing": session.status = "completed_with_errors" if errors else "completed"
    if errors: session.error_summary = {"errors": errors}
    db.commit(); db.refresh(session)
    return session

@router.get("/imports/{import_id}")
def import_status(import_id: int, db: Session = Depends(get_db)):
    item = db.get(ImportSession, import_id)
    if not item: raise HTTPException(404, "Import session not found")
    return item

@router.get("/analytics/fitness")
def fitness(start: date | None = None, end: date | None = None, sport: str | None = None, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    end = end or date.today(); start = start or end - timedelta(days=365)
    stmt = select(func.date(Activity.start_time), func.sum(ActivityMetrics.training_load)).join(ActivityMetrics).where(Activity.athlete_id == athlete.id, Activity.start_time >= datetime.combine(start, datetime.min.time()), Activity.start_time <= datetime.combine(end, datetime.max.time())).group_by(func.date(Activity.start_time))
    if sport: stmt = stmt.where(Activity.sport == sport)
    daily = {date.fromisoformat(str(d)): float(load or 0) for d, load in db.execute(stmt).all()}
    return ewma_series(daily, start, end)

@router.post("/objectives")
def create_objective(payload: ObjectiveCreate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    obj = Objective(athlete_id=athlete.id, **payload.model_dump())
    db.add(obj); db.commit(); db.refresh(obj); return obj

@router.get("/objectives")
def list_objectives(db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    return list(db.scalars(select(Objective).where(Objective.athlete_id == athlete.id).order_by(Objective.event_date)))

@router.post("/planned-workouts")
def create_planned(payload: PlannedWorkoutCreate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    workout = PlannedWorkout(athlete_id=athlete.id, **payload.model_dump())
    db.add(workout); db.commit(); db.refresh(workout); return workout

@router.get("/calendar")
def calendar(start: date, end: date, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    a = list(db.scalars(select(Activity).where(Activity.athlete_id == athlete.id, Activity.start_time >= datetime.combine(start, datetime.min.time()), Activity.start_time <= datetime.combine(end, datetime.max.time())).order_by(Activity.start_time)))
    p = list(db.scalars(select(PlannedWorkout).where(PlannedWorkout.athlete_id == athlete.id, PlannedWorkout.scheduled_at >= datetime.combine(start, datetime.min.time()), PlannedWorkout.scheduled_at <= datetime.combine(end, datetime.max.time())).order_by(PlannedWorkout.scheduled_at)))
    return {"activities": a, "planned": p}
