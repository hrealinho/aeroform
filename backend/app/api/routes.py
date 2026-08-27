from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from app.core.config import settings
from app.db.session import get_db
from app.domain.models import Athlete, Activity, ActivityMetrics, ImportSession, Objective, PlannedWorkout, IntegrationConnection
from app.domain.schemas import ObjectiveCreate, PlannedWorkoutCreate
from app.metrics.fitness import ewma_series
from app.integrations.strava.client import authorization_url, exchange_code, revoke, StravaError
from app.services.oauth_state import create_state, verify_state
from app.services.token_crypto import encrypt_token
from app.tasks.imports import process_uploaded_files, sync_strava_history, sync_strava_activity, remove_strava_activity

router = APIRouter(prefix="/api/v1")


def demo_athlete(db: Session):
    athlete = db.scalar(select(Athlete).where(Athlete.email == "demo@endurance.ai"))
    if athlete is None:
        athlete = Athlete(email="demo@endurance.ai", display_name="Demo Athlete", timezone="Europe/Madrid")
        db.add(athlete)
        db.commit()
        db.refresh(athlete)
    return athlete


def dispatch(task, *args):
    if settings.async_tasks:
        return task.delay(*args).id
    result = task.apply(args=args)
    if result.failed():
        raise result.result
    return result.id


@router.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0", "async_tasks": settings.async_tasks}


@router.get("/activities")
def activities(db: Session = Depends(get_db), limit: int = Query(100, ge=1, le=1000), sport: str | None = None):
    athlete = demo_athlete(db)
    stmt = select(Activity, ActivityMetrics).outerjoin(ActivityMetrics).where(Activity.athlete_id == athlete.id).order_by(Activity.start_time.desc()).limit(limit)
    if sport:
        stmt = stmt.where(Activity.sport == sport)
    rows = db.execute(stmt).all()
    return [{
        "id": a.id,
        "sport": a.sport,
        "subtype": a.subtype,
        "name": a.name,
        "start_time": a.start_time,
        "duration_s": a.duration_s,
        "distance_m": a.distance_m,
        "elevation_gain_m": a.elevation_gain_m,
        "avg_hr": a.avg_hr,
        "avg_power": a.avg_power,
        "normalized_power": a.normalized_power,
        "training_load": m.training_load if m else None,
        "load_method": m.load_method if m else None,
        "load_confidence": m.load_confidence if m else None,
        "mechanical_load": m.mechanical_load if m else None,
        "metric_details": m.details if m else None,
    } for a, m in rows]


@router.post("/imports/files")
async def upload_files(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    if not files:
        raise HTTPException(400, "No files supplied")
    allowed = {".fit", ".gpx", ".tcx", ".zip"}
    stage_dir = Path(settings.storage_path) / "staging" / uuid.uuid4().hex
    stage_dir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    names: list[str] = []
    for upload in files:
        filename = upload.filename or "activity"
        suffix = Path(filename).suffix.lower()
        if suffix not in allowed:
            raise HTTPException(400, f"Unsupported file type: {suffix or filename}")
        path = stage_dir / f"{uuid.uuid4().hex}{suffix}"
        with path.open("wb") as dst:
            while chunk := await upload.read(1024 * 1024):
                dst.write(chunk)
        staged.append(str(path))
        names.append(filename)
    session = ImportSession(
        athlete_id=athlete.id,
        source="upload",
        status="queued",
        discovered_count=len(staged),
        error_summary={"uploaded_files": names},
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    task_id = dispatch(process_uploaded_files, athlete.id, session.id, staged, "upload")
    db.refresh(session)
    return {"id": session.id, "status": session.status, "task_id": task_id, "discovered_count": session.discovered_count, "imported_count": session.imported_count, "duplicate_count": session.duplicate_count, "failed_count": session.failed_count}


@router.get("/imports")
def import_sessions(db: Session = Depends(get_db), limit: int = Query(20, ge=1, le=100)):
    athlete = demo_athlete(db)
    return list(db.scalars(select(ImportSession).where(ImportSession.athlete_id == athlete.id).order_by(ImportSession.created_at.desc()).limit(limit)))


@router.get("/imports/{import_id}")
def import_status(import_id: int, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    item = db.get(ImportSession, import_id)
    if not item or item.athlete_id != athlete.id:
        raise HTTPException(404, "Import session not found")
    return item


@router.get("/strava/connect")
def strava_connect(db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    try:
        return {"authorization_url": authorization_url(create_state(athlete.id))}
    except StravaError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/strava/callback")
def strava_callback(code: str | None = None, state: str | None = None, error: str | None = None, db: Session = Depends(get_db)):
    if error:
        return RedirectResponse(f"{settings.strava_frontend_redirect_uri.split('?')[0]}?strava=denied")
    if not code or not state:
        raise HTTPException(400, "Missing Strava OAuth code/state")
    try:
        athlete_id = verify_state(state)
        payload = exchange_code(code)
    except (ValueError, StravaError) as exc:
        raise HTTPException(400, str(exc)) from exc
    athlete = db.get(Athlete, athlete_id)
    if not athlete:
        raise HTTPException(404, "Athlete not found")
    external = payload.get("athlete") or {}
    connection = db.scalar(select(IntegrationConnection).where(
        IntegrationConnection.athlete_id == athlete.id,
        IntegrationConnection.provider == "strava",
    ))
    if connection is None:
        connection = IntegrationConnection(athlete_id=athlete.id, provider="strava")
        db.add(connection)
    connection.external_athlete_id = str(external.get("id")) if external.get("id") is not None else None
    connection.access_token_encrypted = encrypt_token(payload.get("access_token"))
    connection.refresh_token_encrypted = encrypt_token(payload.get("refresh_token"))
    connection.expires_at = datetime.fromtimestamp(int(payload["expires_at"]), tz=timezone.utc) if payload.get("expires_at") else None
    scopes = payload.get("scope", "")
    connection.scopes = scopes.split() if isinstance(scopes, str) else list(scopes or [])
    connection.status = "connected"
    connection.last_error = None
    connection.metadata_json = {"athlete": {k: external.get(k) for k in ["id", "firstname", "lastname", "username"]}}
    db.commit()
    session = ImportSession(athlete_id=athlete.id, source="strava", status="queued")
    db.add(session)
    db.commit()
    db.refresh(session)
    dispatch(sync_strava_history, athlete.id, session.id)
    return RedirectResponse(settings.strava_frontend_redirect_uri)


@router.get("/strava/status")
def strava_status(db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    connection = db.scalar(select(IntegrationConnection).where(
        IntegrationConnection.athlete_id == athlete.id,
        IntegrationConnection.provider == "strava",
    ))
    if not connection:
        return {"connected": False, "configured": bool(settings.strava_client_id and settings.strava_client_secret)}
    latest_import = db.scalar(select(ImportSession).where(
        ImportSession.athlete_id == athlete.id,
        ImportSession.source == "strava",
    ).order_by(ImportSession.created_at.desc()))
    return {
        "connected": connection.status not in {"disconnected", "revoked"},
        "configured": bool(settings.strava_client_id and settings.strava_client_secret),
        "status": connection.status,
        "external_athlete_id": connection.external_athlete_id,
        "scopes": connection.scopes,
        "last_sync_at": connection.last_sync_at,
        "last_error": connection.last_error,
        "latest_import": {"id": latest_import.id, "status": latest_import.status, "discovered_count": latest_import.discovered_count, "imported_count": latest_import.imported_count, "duplicate_count": latest_import.duplicate_count, "failed_count": latest_import.failed_count} if latest_import else None,
    }


@router.post("/strava/disconnect")
def disconnect_strava(db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    connection = db.scalar(select(IntegrationConnection).where(
        IntegrationConnection.athlete_id == athlete.id,
        IntegrationConnection.provider == "strava",
    ))
    if not connection:
        return {"connected": False}
    try:
        revoke(db, connection)
    except StravaError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"connected": False}


@router.post("/strava/sync")
def start_strava_sync(db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    connection = db.scalar(select(IntegrationConnection).where(
        IntegrationConnection.athlete_id == athlete.id,
        IntegrationConnection.provider == "strava",
    ))
    if not connection:
        raise HTTPException(409, "Strava is not connected")
    session = ImportSession(athlete_id=athlete.id, source="strava", status="queued")
    db.add(session)
    db.commit()
    db.refresh(session)
    task_id = dispatch(sync_strava_history, athlete.id, session.id)
    return {"import_session_id": session.id, "task_id": task_id, "status": session.status}


@router.get("/strava/webhook")
def verify_strava_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    if hub_mode != "subscribe" or hub_verify_token != settings.strava_webhook_verify_token:
        raise HTTPException(403, "Webhook verification failed")
    return {"hub.challenge": hub_challenge}


@router.post("/strava/webhook", status_code=200)
async def strava_webhook(request: Request, db: Session = Depends(get_db)):
    event = await request.json()
    owner_id = str(event.get("owner_id"))
    connection = db.scalar(select(IntegrationConnection).where(
        IntegrationConnection.provider == "strava",
        IntegrationConnection.external_athlete_id == owner_id,
    ))
    if not connection:
        return {"accepted": True}
    if event.get("object_type") == "athlete":
        if (event.get("updates") or {}).get("authorized") == "false":
            connection.status = "revoked"
            connection.access_token_encrypted = None
            connection.refresh_token_encrypted = None
            db.commit()
        return {"accepted": True}
    if event.get("object_type") != "activity":
        return {"accepted": True}
    object_id = int(event["object_id"])
    if event.get("aspect_type") == "delete":
        dispatch(remove_strava_activity, connection.athlete_id, object_id)
    else:
        dispatch(sync_strava_activity, connection.athlete_id, object_id)
    return {"accepted": True}


@router.get("/analytics/fitness")
def fitness(start: date | None = None, end: date | None = None, sport: str | None = None, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    end = end or date.today()
    start = start or end - timedelta(days=365)
    stmt = select(func.date(Activity.start_time), func.sum(ActivityMetrics.training_load)).join(ActivityMetrics).where(
        Activity.athlete_id == athlete.id,
        Activity.start_time >= datetime.combine(start, datetime.min.time()),
        Activity.start_time <= datetime.combine(end, datetime.max.time()),
    ).group_by(func.date(Activity.start_time))
    if sport:
        stmt = stmt.where(Activity.sport == sport)
    daily = {date.fromisoformat(str(d)): float(load or 0) for d, load in db.execute(stmt).all()}
    return ewma_series(daily, start, end)


@router.get("/analytics/weekly")
def weekly_analytics(weeks: int = Query(16, ge=1, le=260), sport: str | None = None, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    start = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    stmt = select(Activity, ActivityMetrics).join(ActivityMetrics).where(Activity.athlete_id == athlete.id, Activity.start_time >= start)
    if sport:
        stmt = stmt.where(Activity.sport == sport)
    buckets: dict[str, dict] = {}
    for activity, metrics in db.execute(stmt):
        monday = (activity.start_time.date() - timedelta(days=activity.start_time.weekday())).isoformat()
        b = buckets.setdefault(monday, {"week": monday, "load": 0.0, "hours": 0.0, "distance_km": 0.0, "elevation_m": 0.0, "mechanical_load": 0.0, "activities": 0})
        b["load"] += metrics.training_load or 0
        b["hours"] += activity.duration_s / 3600
        b["distance_km"] += (activity.distance_m or 0) / 1000
        b["elevation_m"] += activity.elevation_gain_m or 0
        b["mechanical_load"] += metrics.mechanical_load or 0
        b["activities"] += 1
    return [{**b, "load": round(b["load"], 1), "hours": round(b["hours"], 1), "distance_km": round(b["distance_km"], 1), "elevation_m": round(b["elevation_m"]), "mechanical_load": round(b["mechanical_load"], 1)} for _, b in sorted(buckets.items())]


@router.post("/objectives")
def create_objective(payload: ObjectiveCreate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    obj = Objective(athlete_id=athlete.id, **payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/objectives")
def list_objectives(db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    return list(db.scalars(select(Objective).where(Objective.athlete_id == athlete.id).order_by(Objective.event_date)))


@router.post("/planned-workouts")
def create_planned(payload: PlannedWorkoutCreate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    workout = PlannedWorkout(athlete_id=athlete.id, **payload.model_dump())
    db.add(workout)
    db.commit()
    db.refresh(workout)
    return workout


@router.get("/calendar")
def calendar(start: date, end: date, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    activities = list(db.scalars(select(Activity).where(
        Activity.athlete_id == athlete.id,
        Activity.start_time >= datetime.combine(start, datetime.min.time()),
        Activity.start_time <= datetime.combine(end, datetime.max.time()),
    ).order_by(Activity.start_time)))
    planned = list(db.scalars(select(PlannedWorkout).where(
        PlannedWorkout.athlete_id == athlete.id,
        PlannedWorkout.scheduled_at >= datetime.combine(start, datetime.min.time()),
        PlannedWorkout.scheduled_at <= datetime.combine(end, datetime.max.time()),
    ).order_by(PlannedWorkout.scheduled_at)))
    return {"activities": activities, "planned": planned}
