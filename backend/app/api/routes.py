from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
import shutil
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import select, func, update
from app.core.config import settings
from app.core.timeutil import day_end, day_start, to_utc, utcnow
from app.db.session import get_db
from app.domain.models import Athlete, Activity, ActivityMetrics, ImportSession, Objective, PlannedWorkout, TrainingBlock, PlanningConstraint, PlanChangeAudit, IntegrationConnection, AthleteCoachProfile, AIProposal, CoachMessage
from app.domain.schemas import SeasonPlanRequest, GenerateBlockRequest, ObjectiveCreate, TrainingBlockCreate, TrainingBlockUpdate, PlannedWorkoutCreate, PlannedWorkoutUpdate, ManualMatch, PlanningConstraintCreate, CoachProfileUpdate, CoachAsk, GenerateWeekRequest, AdaptWeekRequest, ActivityClassificationUpdate, ThresholdManualCreate, ThresholdEstimateRequest
from app.metrics.fitness import DEFAULT_WARMUP_DAYS, ewma_series
from app.planning.workouts import estimate_planned_load, infer_intensity
from app.planning.projection import project_load_series, projection_warnings, weekly_totals
from app.planning.matching import activity_match_score
from app.integrations.strava.client import authorization_url, exchange_code, revoke, StravaError
from app.integrations.strava.sync import reap_stale_sessions
from app.services.oauth_state import create_state, verify_state
from app.services.token_crypto import encrypt_token
from app.tasks.imports import process_uploaded_files, sync_strava_history, sync_strava_activity, remove_strava_activity, recompute_athlete_metrics
from app.importers.formats import activity_format
from app.ai.context import build_athlete_context, PLAN_HORIZON_DAYS
from app.ai.analyst import analyse_question
from app.ai.provider import resolve_provider
from app.ai.planner import generate_week_seed, adapt_week_seed, generate_block_seed
from app.planning.season import periodise
from app.ai.commands import validate_commands, apply_commands
from app.ai.explain import explain_workout
from app.services.ingestion import recalculate_activity_metrics, threshold as threshold_at
from app.metrics.thresholds import estimate_thresholds, persist_estimates, current_thresholds_and_zones, first_activity_date, upsert_manual_threshold, delete_threshold
from app.metrics.power_profile import curve_payload
from app.metrics.race_predictor import predict as predict_races
from app.services.dedup import activity_fingerprint
from app.services.duplicates import find_duplicate_groups, delete_activity
from app.services.activity_detail import activity_detail

router = APIRouter(prefix="/api/v1")


DEMO_EMAIL = "demo@endurance.ai"


def demo_athlete(db: Session):
    athlete = db.scalar(select(Athlete).where(Athlete.email == DEMO_EMAIL))
    if athlete is not None:
        return athlete
    athlete = Athlete(email=DEMO_EMAIL, display_name="Demo Athlete", timezone="Europe/Madrid")
    db.add(athlete)
    try:
        db.commit()
    except IntegrityError:
        # Two concurrent first requests both tried to seed the demo athlete. The unique
        # email means exactly one wins; the loser re-reads the winner's row.
        db.rollback()
        athlete = db.scalar(select(Athlete).where(Athlete.email == DEMO_EMAIL))
        if athlete is None:
            raise
        return athlete
    db.refresh(athlete)
    return athlete


def dispatch(task, *args):
    """Queue a task, or run it eagerly when ASYNC_TASKS is off.

    Eager execution is synchronous and can take minutes for a large archive, so async
    endpoints must use ``dispatch_async`` instead of calling this directly - otherwise a
    single import blocks the whole event loop.
    """
    if settings.async_tasks:
        return task.delay(*args).id
    result = task.apply(args=args)
    if result.failed():
        raise result.result
    return result.id


async def dispatch_async(task, *args):
    """dispatch() for async endpoints: eager runs are offloaded to a worker thread."""
    if settings.async_tasks:
        return task.delay(*args).id
    return await run_in_threadpool(dispatch, task, *args)


@router.get("/health")
def health():
    return {"status": "ok", "version": settings.app_version, "async_tasks": settings.async_tasks, "ai_provider": settings.ai_provider}


def _terrain_details(metrics: ActivityMetrics | None) -> dict:
    if not metrics or not isinstance(metrics.details, dict):
        return {}
    terrain = metrics.details.get("terrain")
    return terrain if isinstance(terrain, dict) else {}


def _activity_payload(activity: Activity, metrics: ActivityMetrics | None) -> dict:
    details = metrics.details if metrics and isinstance(metrics.details, dict) else {}
    terrain = _terrain_details(metrics)
    return {
        "id": activity.id,
        "sport": activity.sport,
        "subtype": activity.subtype,
        "name": activity.name,
        "start_time": activity.start_time,
        "duration_s": activity.duration_s,
        "distance_m": activity.distance_m,
        "elevation_gain_m": activity.elevation_gain_m,
        "elevation_loss_m": activity.elevation_loss_m,
        "avg_hr": activity.avg_hr,
        "avg_power": activity.avg_power,
        "normalized_power": activity.normalized_power,
        "training_load": metrics.training_load if metrics else None,
        "metabolic_load": details.get("metabolic_load"),
        "load_method": metrics.load_method if metrics else None,
        "load_confidence": metrics.load_confidence if metrics else None,
        "mechanical_load": metrics.mechanical_load if metrics else None,
        "ascent_load": terrain.get("ascent_load"),
        "descent_load": terrain.get("descent_load"),
        "durability_load": terrain.get("durability_load"),
        "vertical_load": terrain.get("vertical_load"),
        "gain_per_km": terrain.get("gain_per_km"),
        "loss_per_km": terrain.get("loss_per_km"),
        "classification": details.get("classification"),
        "metric_details": details,
    }


@router.get("/activities")
def activities(db: Session = Depends(get_db), limit: int = Query(100, ge=1, le=1000), sport: str | None = None):
    athlete = demo_athlete(db)
    stmt = select(Activity, ActivityMetrics).outerjoin(ActivityMetrics).where(Activity.athlete_id == athlete.id)
    if sport:
        stmt = stmt.where(Activity.sport == sport)
    stmt = stmt.order_by(Activity.start_time.desc()).limit(limit)
    return [_activity_payload(a, m) for a, m in db.execute(stmt).all()]



@router.get("/activities/duplicates")
def activity_duplicates(limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    """Probable repeat recordings of the same session, for the athlete to resolve.

    Never merged automatically: each copy has its own provider id, so choosing which to
    keep is the athlete's decision.
    """
    athlete = demo_athlete(db)
    groups = find_duplicate_groups(db, athlete.id, limit)
    return {
        "groups": groups,
        "group_count": len(groups),
        "duplicated_load": round(sum(g["duplicated_load"] for g in groups), 1),
    }


@router.delete("/activities/{activity_id}", status_code=204)
def remove_activity(activity_id: int, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    if not delete_activity(db, athlete.id, activity_id):
        raise HTTPException(404, "Activity not found")


@router.get("/activities/{activity_id}")
def activity_detail_route(activity_id: int, db: Session = Depends(get_db)):
    """Full detail for one activity: summary, load breakdown, zones, laps and streams."""
    athlete = demo_athlete(db)
    detail = activity_detail(db, athlete.id, activity_id)
    if detail is None:
        raise HTTPException(404, "Activity not found")
    return detail

@router.patch("/activities/{activity_id}/classification")
def update_activity_classification(activity_id: int, payload: ActivityClassificationUpdate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    activity = db.get(Activity, activity_id)
    if not activity or activity.athlete_id != athlete.id:
        raise HTTPException(404, "Activity not found")
    new_fp = activity_fingerprint(payload.sport, activity.start_time, activity.duration_s, activity.distance_m)
    collision = db.scalar(select(Activity).where(
        Activity.athlete_id == athlete.id,
        Activity.fingerprint == new_fp,
        Activity.id != activity.id,
    ))
    if collision:
        raise HTTPException(409, f"Classification would collide with activity {collision.id}")
    activity.sport = payload.sport
    activity.subtype = payload.subtype
    activity.fingerprint = new_fp
    recalculate_activity_metrics(db, athlete.id, activity, {
        "classification": {
            "sport": payload.sport,
            "confidence": "high",
            "reason": "user override",
            "source": "manual",
        }
    })
    db.commit()
    db.refresh(activity)
    return _activity_payload(activity, activity.metrics)


@router.post("/imports/files")
async def upload_files(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    if not files:
        raise HTTPException(400, "No files supplied")
    stage_dir = Path(settings.storage_path) / "staging" / uuid.uuid4().hex
    stage_dir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    names: list[str] = []
    try:
        for upload in files:
            filename = upload.filename or "activity"
            fmt = activity_format(filename)
            is_zip = filename.lower().endswith(".zip")
            if not fmt and not is_zip:
                raise HTTPException(400, f"Unsupported file type: {filename}")
            suffix = ".zip" if is_zip else fmt[0] + (".gz" if fmt[1] else "")
            path = stage_dir / f"{uuid.uuid4().hex}{suffix}"
            with path.open("wb") as dst:
                while chunk := await upload.read(1024 * 1024):
                    dst.write(chunk)
            staged.append(str(path))
            names.append(filename)
    except Exception:
        # A rejected file part-way through a batch used to leave every already-written
        # file on disk with nothing tracking it.
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise
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
    try:
        # Offloaded: with ASYNC_TASKS=false this executes the whole import inline, which
        # would otherwise block the event loop for the duration of the archive.
        task_id = await dispatch_async(process_uploaded_files, athlete.id, session.id, staged, "upload")
    except Exception as exc:
        shutil.rmtree(stage_dir, ignore_errors=True)
        session.status = "failed"
        session.error_summary = {"uploaded_files": names, "errors": [{"error": str(exc)[:500]}]}
        db.commit()
        raise HTTPException(500, f"Import could not be started: {str(exc)[:200]}") from exc
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
    dispatch(sync_strava_history, athlete.id, session.id, None)
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


def _active_strava_import(db: Session, athlete_id: int):
    # Clear abandoned sessions first, otherwise a sync that died mid-run blocks every
    # subsequent attempt with "already running".
    reap_stale_sessions(db, athlete_id)
    return db.scalar(select(ImportSession).where(
        ImportSession.athlete_id == athlete_id,
        ImportSession.source == "strava",
        ImportSession.status.in_(["queued", "processing", "paused_rate_limit"]),
    ).order_by(ImportSession.created_at.desc()))


@router.post("/strava/sync")
def start_strava_sync(after_days: int | None = Query(None, ge=1, le=7300), db: Session = Depends(get_db)):
    """Start a historical Strava backfill.

    ``after_days`` scopes the window. With STRAVA_SYNC_STREAMS on, every activity costs
    an extra API call, so a bounded window is usually what you want.
    """
    athlete = demo_athlete(db)
    connection = db.scalar(select(IntegrationConnection).where(
        IntegrationConnection.athlete_id == athlete.id,
        IntegrationConnection.provider == "strava",
    ))
    if not connection:
        raise HTTPException(409, "Strava is not connected")
    active = _active_strava_import(db, athlete.id)
    if active:
        return {"import_session_id": active.id, "task_id": None, "status": active.status, "already_running": True}
    session = ImportSession(athlete_id=athlete.id, source="strava", status="queued")
    db.add(session)
    db.commit()
    db.refresh(session)
    task_id = dispatch(sync_strava_history, athlete.id, session.id, after_days)
    return {"import_session_id": session.id, "task_id": task_id, "status": session.status, "after_days": after_days}


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
    # Strava retries anything that is not a 2xx, so a malformed payload must be accepted
    # and dropped rather than raising. Every field is treated as untrusted.
    try:
        event = await request.json()
    except Exception:
        return {"accepted": True, "ignored": "unparseable payload"}
    if not isinstance(event, dict):
        return {"accepted": True, "ignored": "unexpected payload shape"}
    if event.get("owner_id") is None:
        return {"accepted": True, "ignored": "missing owner_id"}
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
    try:
        object_id = int(event["object_id"])
    except (KeyError, TypeError, ValueError):
        return {"accepted": True, "ignored": "missing or non-numeric object_id"}
    if event.get("aspect_type") == "delete":
        await dispatch_async(remove_strava_activity, connection.athlete_id, object_id)
    else:
        await dispatch_async(sync_strava_activity, connection.athlete_id, object_id)
    return {"accepted": True}


LoadKind = Literal["overall", "metabolic", "mechanical", "ascent", "descent", "durability"]


def _load_component(metrics: ActivityMetrics, kind: str) -> float:
    details = metrics.details if isinstance(metrics.details, dict) else {}
    terrain = details.get("terrain") if isinstance(details.get("terrain"), dict) else {}
    if kind == "overall":
        return float(metrics.training_load or 0)
    if kind == "metabolic":
        return float(details.get("metabolic_load") or metrics.training_load or 0)
    if kind == "mechanical":
        return float(metrics.mechanical_load or 0)
    if kind == "ascent":
        return float(terrain.get("ascent_load") or 0)
    if kind == "descent":
        return float(terrain.get("descent_load") or 0)
    if kind == "durability":
        return float(terrain.get("durability_load") or 0)
    # Unreachable: load_kind is constrained by the LoadKind literal on the query param.
    raise HTTPException(400, f"Unsupported load_kind: {kind}")


@router.get("/analytics/fitness")
def fitness(
    start: date | None = None,
    end: date | None = None,
    sport: str | None = None,
    load_kind: LoadKind = Query("overall"),
    db: Session = Depends(get_db),
):
    athlete = demo_athlete(db)
    end = end or date.today()
    start = start or end - timedelta(days=365)
    # Load the warm-up period as well as the visible range. The EWMA starts at zero, so
    # without prior load a narrowed window reports fitness climbing from nothing instead
    # of the athlete's actual accumulated state.
    warmup_start = start - timedelta(days=DEFAULT_WARMUP_DAYS)
    stmt = select(Activity, ActivityMetrics).join(ActivityMetrics).where(
        Activity.athlete_id == athlete.id,
        Activity.start_time >= day_start(warmup_start),
        Activity.start_time <= day_end(end),
    )
    if sport:
        stmt = stmt.where(Activity.sport == sport)
    daily: dict[date, float] = {}
    for activity, metrics in db.execute(stmt):
        day = to_utc(activity.start_time).date()
        daily[day] = daily.get(day, 0.0) + _load_component(metrics, load_kind)
    return ewma_series(daily, start, end, warmup_days=DEFAULT_WARMUP_DAYS)


@router.get("/analytics/weekly")
def weekly_analytics(weeks: int = Query(16, ge=1, le=260), sport: str | None = None, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    start = utcnow() - timedelta(weeks=weeks)
    stmt = select(Activity, ActivityMetrics).join(ActivityMetrics).where(Activity.athlete_id == athlete.id, Activity.start_time >= start)
    if sport:
        stmt = stmt.where(Activity.sport == sport)
    buckets: dict[str, dict] = {}
    for activity, metrics in db.execute(stmt):
        started = to_utc(activity.start_time)
        monday = (started.date() - timedelta(days=started.weekday())).isoformat()
        b = buckets.setdefault(monday, {
            "week": monday, "load": 0.0, "metabolic_load": 0.0, "mechanical_load": 0.0,
            "ascent_load": 0.0, "descent_load": 0.0, "durability_load": 0.0,
            "hours": 0.0, "distance_km": 0.0, "elevation_gain_m": 0.0, "elevation_loss_m": 0.0,
            "activities": 0,
        })
        b["load"] += _load_component(metrics, "overall")
        b["metabolic_load"] += _load_component(metrics, "metabolic")
        b["mechanical_load"] += _load_component(metrics, "mechanical")
        b["ascent_load"] += _load_component(metrics, "ascent")
        b["descent_load"] += _load_component(metrics, "descent")
        b["durability_load"] += _load_component(metrics, "durability")
        b["hours"] += activity.duration_s / 3600
        b["distance_km"] += (activity.distance_m or 0) / 1000
        b["elevation_gain_m"] += activity.elevation_gain_m or 0
        b["elevation_loss_m"] += activity.elevation_loss_m or 0
        b["activities"] += 1
    result = []
    for _, b in sorted(buckets.items()):
        for key in ("load", "metabolic_load", "mechanical_load", "ascent_load", "descent_load", "durability_load", "hours", "distance_km"):
            b[key] = round(b[key], 1)
        b["elevation_gain_m"] = round(b["elevation_gain_m"])
        b["elevation_loss_m"] = round(b["elevation_loss_m"])
        # Backward-compatible alias used by the v0.4 UI/client.
        b["elevation_m"] = b["elevation_gain_m"]
        result.append(b)
    return result


@router.get("/thresholds")
def list_thresholds(db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    return current_thresholds_and_zones(db, athlete.id)


def _threshold_effective_from(db: Session, athlete_id: int, requested: date | None, cover_history: bool) -> date:
    """Resolve the date a threshold starts applying from.

    A threshold only applies to activities on or after its valid_from, so defaulting to
    today means a freshly entered FTP or resting HR never affects a single stored
    activity. When the caller wants history covered, back-date to the first activity.
    """
    if requested is not None:
        return requested
    if not cover_history:
        return date.today()
    return first_activity_date(db, athlete_id) or date.today()


@router.post("/thresholds/estimate")
def estimate_athlete_thresholds(payload: ThresholdEstimateRequest, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    estimates = estimate_thresholds(db, athlete.id, payload.history_days)
    saved = []
    task_id = None
    if payload.persist:
        effective = _threshold_effective_from(db, athlete.id, None, payload.apply_to_history)
        saved = persist_estimates(db, athlete.id, estimates, valid_from=effective, apply_if_manual_missing=True)
        if payload.apply_to_history and saved:
            # Recomputing a whole history inside the request both blocked the worker and
            # was never committed, so the backfill silently did nothing. It now runs as a
            # task that commits per activity, exactly like an import.
            task_id = dispatch(recompute_athlete_metrics, athlete.id, None)
    return {
        "estimates": [e.as_dict() for e in estimates],
        "saved": len(saved),
        "effective_from": saved[0].valid_from.isoformat() if saved else None,
        "recompute_task_id": task_id,
        **current_thresholds_and_zones(db, athlete.id),
    }


@router.post("/thresholds/manual")
def create_manual_threshold(payload: ThresholdManualCreate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    # Default to covering the athlete's whole history: a manual threshold that only
    # applies from today cannot change any load already computed, which is never what
    # someone entering their FTP expects.
    effective = _threshold_effective_from(db, athlete.id, payload.valid_from, payload.apply_to_history)
    upsert_manual_threshold(db, athlete.id, payload.sport, payload.metric, payload.value, effective)
    task_id = dispatch(recompute_athlete_metrics, athlete.id, None) if payload.recompute else None
    return {"effective_from": effective.isoformat(), "recompute_task_id": task_id, **current_thresholds_and_zones(db, athlete.id)}


@router.delete("/thresholds/{threshold_id}")
def remove_threshold(threshold_id: int, recompute: bool = True, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    if not delete_threshold(db, athlete.id, threshold_id):
        raise HTTPException(404, "Threshold not found")
    task_id = dispatch(recompute_athlete_metrics, athlete.id, None) if recompute else None
    return {"deleted": threshold_id, "recompute_task_id": task_id, **current_thresholds_and_zones(db, athlete.id)}


@router.post("/thresholds/recompute")
def recompute_from_thresholds(since_days: int | None = None, db: Session = Depends(get_db)):
    """Recalculate stored metrics so threshold changes reach historical activities."""
    athlete = demo_athlete(db)
    return {"task_id": dispatch(recompute_athlete_metrics, athlete.id, since_days)}



@router.get("/power/profile")
def power_profile(
    days: int = Query(365, ge=7, le=3650),
    compare_days: int | None = Query(None, ge=7, le=3650),
    db: Session = Depends(get_db),
):
    """Mean-maximal power curve, critical power and rider type for cycling."""
    athlete = demo_athlete(db)
    weight = threshold_at(db, athlete.id, "global", "weight_kg", date.today())
    payload = curve_payload(db, athlete.id, weight, days, compare_days)
    if not payload["has_data"]:
        payload["advice"] = (
            "A power curve needs per-second power streams. Set STRAVA_SYNC_STREAMS=true and "
            "re-sync, or import the original FIT files, then recompute."
        )
    if weight is None:
        payload["weight_advice"] = (
            "Set a weight to see watts per kilo (sport: global, metric: weight_kg). It is stored "
            "with an effective date, so historical values stay correct."
        )
    return payload


@router.get("/running/predictions")
def running_predictions(days: int = Query(365, ge=30, le=3650), db: Session = Depends(get_db)):
    """Predicted 5K, 10K, half and full marathon times from recorded efforts."""
    athlete = demo_athlete(db)
    critical_speed = threshold_at(db, athlete.id, "running", "threshold_speed_mps", date.today())
    return predict_races(db, athlete.id, days, critical_speed)

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


@router.delete("/objectives/{objective_id}", status_code=204)
def delete_objective(objective_id: int, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    obj = db.get(Objective, objective_id)
    if not obj or obj.athlete_id != athlete.id:
        raise HTTPException(404, "Objective not found")
    # Detach dependants first. Without this, Postgres rejects the delete outright with a
    # foreign-key violation and SQLite (which does not enforce them by default) leaves
    # planned workouts and blocks pointing at a row that no longer exists.
    db.execute(update(PlannedWorkout).where(
        PlannedWorkout.athlete_id == athlete.id, PlannedWorkout.objective_id == obj.id,
    ).values(objective_id=None))
    db.execute(update(TrainingBlock).where(
        TrainingBlock.athlete_id == athlete.id, TrainingBlock.objective_id == obj.id,
    ).values(objective_id=None))
    db.delete(obj)
    db.commit()


@router.post("/training-blocks")
def create_training_block(payload: TrainingBlockCreate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    block = TrainingBlock(athlete_id=athlete.id, **payload.model_dump())
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


@router.get("/training-blocks")
def list_training_blocks(start: date | None = None, end: date | None = None, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    stmt = select(TrainingBlock).where(TrainingBlock.athlete_id == athlete.id)
    if start:
        stmt = stmt.where(TrainingBlock.end_date >= start)
    if end:
        stmt = stmt.where(TrainingBlock.start_date <= end)
    return list(db.scalars(stmt.order_by(TrainingBlock.start_date)))


@router.patch("/training-blocks/{block_id}")
def update_training_block(block_id: int, payload: TrainingBlockUpdate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    block = db.get(TrainingBlock, block_id)
    if not block or block.athlete_id != athlete.id:
        raise HTTPException(404, "Training block not found")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(block, key, value)
    if block.end_date < block.start_date:
        raise HTTPException(422, "end_date must be on or after start_date")
    db.commit()
    db.refresh(block)
    return block


@router.delete("/training-blocks/{block_id}", status_code=204)
def delete_training_block(block_id: int, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    block = db.get(TrainingBlock, block_id)
    if not block or block.athlete_id != athlete.id:
        raise HTTPException(404, "Training block not found")
    db.execute(update(PlannedWorkout).where(
        PlannedWorkout.athlete_id == athlete.id, PlannedWorkout.block_id == block.id,
    ).values(block_id=None))
    db.delete(block)
    db.commit()


def _workout_dict(workout: PlannedWorkout) -> dict:
    return {
        "id": workout.id, "athlete_id": workout.athlete_id, "block_id": workout.block_id, "objective_id": workout.objective_id,
        "scheduled_at": workout.scheduled_at, "sport": workout.sport, "name": workout.name, "duration_s": workout.duration_s,
        "distance_m": workout.distance_m, "elevation_m": workout.elevation_m, "projected_load": workout.projected_load,
        "steps": workout.steps or [], "locked": workout.locked, "matched_activity_id": workout.matched_activity_id,
        "intensity": infer_intensity(workout.steps),
    }


def _audit(db: Session, athlete_id: int, workout: PlannedWorkout, action: str, before: dict | None, reason: str | None = None):
    # before_state/after_state are JSON columns, so every value has to survive
    # json.dumps. _workout_dict carries a real datetime for scheduled_at (which FastAPI
    # encodes on the way out but SQLAlchemy cannot), so encode it here. Without this,
    # every create/update/delete/match raised "Object of type datetime is not JSON
    # serializable" and returned 500.
    db.add(PlanChangeAudit(
        athlete_id=athlete_id, entity_type="planned_workout", entity_id=workout.id, action=action,
        before_state=jsonable_encoder(before), after_state=jsonable_encoder(_workout_dict(workout)) if action != "delete" else None,
        reason=reason, initiated_by="user",
    ))


def _validate_hard_constraints(db: Session, athlete_id: int, scheduled_at: datetime, duration_s: float | None):
    day = scheduled_at.date()
    constraints = db.scalars(select(PlanningConstraint).where(
        PlanningConstraint.athlete_id == athlete_id, PlanningConstraint.enabled.is_(True),
        PlanningConstraint.start_date <= day,
    )).all()
    for constraint in constraints:
        if constraint.end_date and day > constraint.end_date:
            continue
        if constraint.weekdays and day.weekday() not in constraint.weekdays:
            continue
        value = constraint.value or {}
        hard = value.get("hard", True)
        if not hard:
            continue
        if constraint.constraint_type == "unavailable":
            raise HTTPException(409, f"Date conflicts with unavailable period (constraint {constraint.id}).")
        if constraint.constraint_type in {"max_daily_hours", "availability"}:
            max_hours = value.get("max_hours")
            if max_hours is not None and float(duration_s or 0) > float(max_hours) * 3600:
                raise HTTPException(409, f"Workout exceeds configured {max_hours}h availability on this day.")


@router.post("/planned-workouts")
def create_planned(payload: PlannedWorkoutCreate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    values = payload.model_dump(exclude={"intensity"})
    if values.get("projected_load") is None:
        estimate = estimate_planned_load(values.get("duration_s"), values.get("steps"), payload.intensity)
        values["projected_load"] = estimate.load
    if payload.intensity and not values.get("steps"):
        values["steps"] = [{"type": "main", "intensity": payload.intensity, "duration_s": values.get("duration_s")} ]
    _validate_hard_constraints(db, athlete.id, values["scheduled_at"], values.get("duration_s"))
    workout = PlannedWorkout(athlete_id=athlete.id, **values)
    db.add(workout)
    db.flush()
    _audit(db, athlete.id, workout, "create", None, "Workout created")
    db.commit()
    db.refresh(workout)
    return _workout_dict(workout)


@router.get("/planned-workouts")
def list_planned(start: datetime | None = None, end: datetime | None = None, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    stmt = select(PlannedWorkout).where(PlannedWorkout.athlete_id == athlete.id)
    if start:
        stmt = stmt.where(PlannedWorkout.scheduled_at >= start)
    if end:
        stmt = stmt.where(PlannedWorkout.scheduled_at <= end)
    return [_workout_dict(w) for w in db.scalars(stmt.order_by(PlannedWorkout.scheduled_at))]


@router.patch("/planned-workouts/{workout_id}")
def update_planned(workout_id: int, payload: PlannedWorkoutUpdate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    workout = db.get(PlannedWorkout, workout_id)
    if not workout or workout.athlete_id != athlete.id:
        raise HTTPException(404, "Planned workout not found")
    before = _workout_dict(workout)
    values = payload.model_dump(exclude_unset=True, exclude={"intensity"})
    # A lock protects a workout from incidental edits, but the owner may deliberately
    # unlock and edit in one request by sending locked=false alongside the change. The
    # AI path cannot do this: validate_commands rejects any command touching a locked
    # workout regardless of what else the command contains.
    prescription_changed = any(k in values for k in {"scheduled_at", "sport", "duration_s", "steps"}) or payload.intensity is not None
    if workout.locked and prescription_changed and payload.locked is not False:
        raise HTTPException(409, "Workout is locked. Send locked=false with your change, or unlock it first.")
    candidate_scheduled = values.get("scheduled_at", workout.scheduled_at)
    candidate_duration = values.get("duration_s", workout.duration_s)
    _validate_hard_constraints(db, athlete.id, candidate_scheduled, candidate_duration)
    for key, value in values.items():
        setattr(workout, key, value)
    if payload.intensity is not None:
        workout.steps = [{"type": "main", "intensity": payload.intensity, "duration_s": workout.duration_s}]
    if payload.projected_load is None and (any(k in values for k in {"duration_s", "steps"}) or payload.intensity is not None):
        workout.projected_load = estimate_planned_load(workout.duration_s, workout.steps, payload.intensity).load
    _audit(db, athlete.id, workout, "update", before, "Workout edited or moved")
    db.commit()
    db.refresh(workout)
    return _workout_dict(workout)


@router.delete("/planned-workouts/{workout_id}", status_code=204)
def delete_planned(workout_id: int, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    workout = db.get(PlannedWorkout, workout_id)
    if not workout or workout.athlete_id != athlete.id:
        raise HTTPException(404, "Planned workout not found")
    if workout.locked:
        raise HTTPException(409, "Locked workouts cannot be deleted")
    before = _workout_dict(workout)
    _audit(db, athlete.id, workout, "delete", before, "Workout deleted")
    db.delete(workout)
    db.commit()


@router.post("/planned-workouts/{workout_id}/match")
def manual_match(workout_id: int, payload: ManualMatch, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    workout = db.get(PlannedWorkout, workout_id)
    if not workout or workout.athlete_id != athlete.id:
        raise HTTPException(404, "Planned workout not found")
    before = _workout_dict(workout)
    if payload.activity_id is not None:
        activity = db.get(Activity, payload.activity_id)
        if not activity or activity.athlete_id != athlete.id:
            raise HTTPException(404, "Activity not found")
        workout.matched_activity_id = activity.id
    else:
        workout.matched_activity_id = None
    _audit(db, athlete.id, workout, "match", before, "Manual planned-to-actual match")
    db.commit()
    return _workout_dict(workout)


@router.post("/matching/auto")
def auto_match(start: date | None = None, end: date | None = None, threshold: float = Query(0.72, ge=0, le=1), db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    end = end or date.today()
    start = start or end - timedelta(days=60)
    workouts = list(db.scalars(select(PlannedWorkout).where(
        PlannedWorkout.athlete_id == athlete.id, PlannedWorkout.matched_activity_id.is_(None),
        PlannedWorkout.scheduled_at >= day_start(start), PlannedWorkout.scheduled_at <= day_end(end),
    )))
    activities = list(db.scalars(select(Activity).where(
        Activity.athlete_id == athlete.id, Activity.start_time >= day_start(start - timedelta(days=2)),
        Activity.start_time <= day_end(end + timedelta(days=2)),
    )))
    already_used = {w.matched_activity_id for w in db.scalars(select(PlannedWorkout).where(PlannedWorkout.athlete_id == athlete.id)) if w.matched_activity_id}
    matches = []
    for workout in workouts:
        best = None
        for activity in activities:
            if activity.id in already_used:
                continue
            # activity_match_score normalizes both timestamps to UTC itself, so mixed
            # naive/aware values coming back from SQLite no longer need patching here.
            result = activity_match_score(workout, activity)
            if not best or result.score > best[0].score:
                best = (result, activity)
        if best and best[0].score >= threshold:
            result, activity = best
            workout.matched_activity_id = activity.id
            already_used.add(activity.id)
            matches.append({"workout_id": workout.id, "activity_id": activity.id, "score": result.score, "reasons": result.reasons})
    db.commit()
    return {"matched": len(matches), "matches": matches}


@router.get("/calendar")
def calendar(start: date, end: date, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    activity_rows = db.execute(select(Activity, ActivityMetrics).outerjoin(ActivityMetrics).where(
        Activity.athlete_id == athlete.id,
        Activity.start_time >= day_start(start),
        Activity.start_time <= day_end(end),
    ).order_by(Activity.start_time)).all()
    planned = list(db.scalars(select(PlannedWorkout).where(
        PlannedWorkout.athlete_id == athlete.id,
        PlannedWorkout.scheduled_at >= day_start(start),
        PlannedWorkout.scheduled_at <= day_end(end),
    ).order_by(PlannedWorkout.scheduled_at)))
    objectives = list(db.scalars(select(Objective).where(Objective.athlete_id == athlete.id, Objective.event_date >= start, Objective.event_date <= end)))
    blocks = list(db.scalars(select(TrainingBlock).where(TrainingBlock.athlete_id == athlete.id, TrainingBlock.end_date >= start, TrainingBlock.start_date <= end)))
    activities = [{
        "id": a.id, "sport": a.sport, "name": a.name, "start_time": a.start_time, "duration_s": a.duration_s,
        "distance_m": a.distance_m, "elevation_gain_m": a.elevation_gain_m, "training_load": m.training_load if m else 0,
        "matched_workout_id": next((w.id for w in planned if w.matched_activity_id == a.id), None),
    } for a, m in activity_rows]
    return {"activities": activities, "planned": [_workout_dict(w) for w in planned], "objectives": objectives, "blocks": blocks}


@router.get("/analytics/projection")
def projection(start: date | None = None, end: date | None = None, sport: str | None = None, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    today = date.today()
    start = start or today - timedelta(days=90)
    end = end or today + timedelta(days=90)
    # Warm up the EWMA with up to one year before the visible range so projections do not start from zero.
    calculation_start = min(start, today) - timedelta(days=365)
    stmt = select(func.date(Activity.start_time), func.sum(ActivityMetrics.training_load)).join(ActivityMetrics).where(
        Activity.athlete_id == athlete.id, Activity.start_time >= day_start(calculation_start),
        Activity.start_time <= day_end(min(end, today)),
    ).group_by(func.date(Activity.start_time))
    if sport:
        stmt = stmt.where(Activity.sport == sport)
    actual = {date.fromisoformat(str(d)): float(load or 0) for d, load in db.execute(stmt).all()}
    p_stmt = select(PlannedWorkout).where(
        PlannedWorkout.athlete_id == athlete.id, PlannedWorkout.scheduled_at >= day_start(calculation_start),
        PlannedWorkout.scheduled_at <= day_end(end),
    )
    if sport:
        p_stmt = p_stmt.where(PlannedWorkout.sport == sport)
    planned_daily = {}
    key_sessions = []
    for workout in db.scalars(p_stmt):
        d = workout.scheduled_at.date()
        planned_daily[d] = planned_daily.get(d, 0.0) + float(workout.projected_load or 0)
        key_sessions.append((workout.scheduled_at, infer_intensity(workout.steps), float(workout.projected_load or 0)))
    full = project_load_series(actual, planned_daily, calculation_start, end)
    visible = [row for row in full if row["date"] >= start.isoformat()]
    return {"series": visible, "weeks": weekly_totals(visible), "warnings": projection_warnings(visible, key_sessions)}


@router.post("/planning-constraints")
def create_constraint(payload: PlanningConstraintCreate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    constraint = PlanningConstraint(athlete_id=athlete.id, **payload.model_dump())
    db.add(constraint)
    db.commit()
    db.refresh(constraint)
    return constraint


@router.get("/planning-constraints")
def list_constraints(db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    return list(db.scalars(select(PlanningConstraint).where(PlanningConstraint.athlete_id == athlete.id, PlanningConstraint.enabled.is_(True)).order_by(PlanningConstraint.start_date)))


@router.get("/plan-audit")
def plan_audit(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    return list(db.scalars(select(PlanChangeAudit).where(PlanChangeAudit.athlete_id == athlete.id).order_by(PlanChangeAudit.created_at.desc()).limit(limit)))



def _coach_profile_dict(profile: AthleteCoachProfile | None) -> dict:
    preferences = (profile.preferences if profile else None) or {}
    return {
        "prescription": preferences.get("prescription", "auto"),
        "available_hours_per_week": profile.available_hours_per_week if profile else None,
        "preferred_long_day": profile.preferred_long_day if profile else 5,
        "preferred_rest_day": profile.preferred_rest_day if profile else 0,
        "doubles_allowed": profile.doubles_allowed if profile else False,
        "preferences": profile.preferences if profile else {},
    }


def _proposal_dict(p: AIProposal) -> dict:
    return {
        "id": p.id, "proposal_type": p.proposal_type, "status": p.status, "title": p.title,
        "summary": p.summary, "commands": p.commands or [], "validation": p.validation or {},
        "provider": p.provider, "created_at": p.created_at, "decided_at": p.decided_at,
    }


def _compact_context(context: dict) -> dict:
    return {
        "as_of": context.get("as_of"), "state": context.get("state"), "objectives": context.get("objectives"),
        "current_block": context.get("current_block"), "profile": context.get("profile"),
        "recent_weeks": context.get("recent_weeks", [])[-8:], "adherence_28d_pct": context.get("adherence_28d_pct"),
        # Kept so the audit trail shows the plan the model was actually reasoning over.
        "planned_count": len(context.get("planned") or []),
        "plan_horizon_days": PLAN_HORIZON_DAYS,
    }


@router.get("/coach/profile")
def coach_profile(db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    profile = db.scalar(select(AthleteCoachProfile).where(AthleteCoachProfile.athlete_id == athlete.id))
    return _coach_profile_dict(profile)


@router.put("/coach/profile")
def update_coach_profile(payload: CoachProfileUpdate, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    profile = db.scalar(select(AthleteCoachProfile).where(AthleteCoachProfile.athlete_id == athlete.id))
    if profile is None:
        profile = AthleteCoachProfile(athlete_id=athlete.id)
        db.add(profile)
    values = payload.model_dump()
    # prescription lives inside preferences so no column has to be added.
    prescription = values.pop("prescription", "auto")
    for key, value in values.items():
        setattr(profile, key, value)
    profile.preferences = {**(profile.preferences or {}), "prescription": prescription}
    db.commit(); db.refresh(profile)
    return _coach_profile_dict(profile)


@router.get("/coach/context")
def coach_context(db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    return build_athlete_context(db, athlete)


@router.post("/coach/ask")
def coach_ask(payload: CoachAsk, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    context = build_athlete_context(db, athlete)
    draft = analyse_question(context, payload.question)
    provider, provider_error = resolve_provider()
    if provider_error is None:
        try:
            result = provider.synthesize_analysis(payload.question, context, draft)
        except Exception as exc:
            result = draft
            provider_error = str(exc)[:300]
    else:
        result = draft
    db.add(CoachMessage(athlete_id=athlete.id, role="user", content=payload.question, evidence=[]))
    assistant = CoachMessage(athlete_id=athlete.id, role="assistant", content=result["answer"], evidence=result.get("evidence") or [])
    db.add(assistant); db.commit(); db.refresh(assistant)
    return {
        "id": assistant.id, "answer": result["answer"], "evidence": result.get("evidence") or [],
        "confidence": result.get("confidence", "medium"), "provider": getattr(provider, "name", "local"),
        "provider_error": provider_error,
    }


@router.get("/coach/messages")
def coach_messages(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    rows = list(db.scalars(select(CoachMessage).where(CoachMessage.athlete_id == athlete.id).order_by(CoachMessage.created_at.desc()).limit(limit)))
    rows.reverse()
    return [{"id": m.id, "role": m.role, "content": m.content, "evidence": m.evidence or [], "created_at": m.created_at} for m in rows]


@router.post("/coach/generate-week")
def coach_generate_week(payload: GenerateWeekRequest, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    context = build_athlete_context(db, athlete)
    today = date.today()
    week_start = payload.week_start or (today + timedelta(days=(7 - today.weekday()) % 7 or 7))
    week_start = week_start - timedelta(days=week_start.weekday())
    seed_summary, seed_commands = generate_week_seed(
        context, week_start, payload.objective_id, payload.strategy,
        intent=None if payload.intent in (None, "auto") else payload.intent)
    provider, provider_error = resolve_provider()
    try:
        if provider_error:
            raise RuntimeError(provider_error)
        plan = provider.refine_plan("generate_week", context, seed_summary, seed_commands)
        commands, summary, provider_name = plan.commands, plan.summary, plan.provider
    except Exception as exc:
        commands, summary, provider_name = seed_commands, seed_summary + f" Provider refinement was unavailable ({str(exc)[:120]}).", "local_fallback"
    validation = validate_commands(db, athlete.id, commands)
    if not validation["valid"] and commands != seed_commands:
        seed_validation = validate_commands(db, athlete.id, seed_commands)
        if seed_validation["valid"]:
            commands, validation, provider_name = seed_commands, seed_validation, "local_fallback"
            summary += " The remote proposal failed deterministic validation, so the validated local seed was kept."
    proposal = AIProposal(
        athlete_id=athlete.id, proposal_type="generate_week", status="pending", title=f"Week of {week_start.isoformat()}",
        summary=summary, commands=validation.get("commands") or commands, context_snapshot=_compact_context(context),
        validation=validation, provider=provider_name,
    )
    db.add(proposal); db.commit(); db.refresh(proposal)
    return _proposal_dict(proposal)



def _long_objective(objective: Objective | None) -> bool:
    """Long events need a two-week taper. Duration beats distance: a 40 km mountain
    ultra is a longer day than a road marathon."""
    if objective is None:
        return False
    if objective.expected_duration_s and objective.expected_duration_s >= 4 * 3600:
        return True
    if objective.sport in {"trail_running", "mountaineering"} and (objective.distance_m or 0) >= 42000:
        return True
    return (objective.distance_m or 0) >= 42000 or (objective.elevation_m or 0) >= 2500


@router.post("/coach/plan-season")
def plan_season(payload: SeasonPlanRequest, db: Session = Depends(get_db)):
    """Derive a periodised block structure from an objective.

    This is the layer between an objective and individual workouts: it decides how many
    weeks of base, build, specific, peak and taper fit before the race, and what weekly
    load each of those weeks should carry. Returns a preview unless apply is set.
    """
    athlete = demo_athlete(db)
    context = build_athlete_context(db, athlete)

    objective = None
    if payload.objective_id is not None:
        objective = db.get(Objective, payload.objective_id)
        if not objective or objective.athlete_id != athlete.id:
            raise HTTPException(404, "Objective not found")
    else:
        objective = db.scalar(select(Objective).where(
            Objective.athlete_id == athlete.id, Objective.event_date >= date.today(),
        ).order_by(Objective.priority, Objective.event_date))
    if objective is None:
        raise HTTPException(409, "Add an objective first: a season plan is built backwards from a race date.")

    start = payload.start_date or date.today()
    if objective.event_date <= start:
        raise HTTPException(409, "The objective date is in the past relative to the plan start.")

    state = context.get("state") or {}
    blocks = periodise(
        start=start, objective_date=objective.event_date,
        typical_weekly_load=float(state.get("typical_weekly_load_8w") or 0),
        typical_weekly_hours=float(state.get("typical_weekly_hours_8w") or 0),
        long_objective=_long_objective(objective), objective_name=objective.name,
    )
    if not blocks:
        raise HTTPException(409, "Not enough time before the objective to build a plan.")

    preview = [b.as_dict() for b in blocks]
    applied = []
    if payload.apply:
        horizon_start, horizon_end = blocks[0].start, blocks[-1].end
        if payload.replace_existing:
            # Stacking a new plan on top of an old one leaves overlapping blocks and the
            # week planner cannot tell which target applies.
            for existing in db.scalars(select(TrainingBlock).where(
                TrainingBlock.athlete_id == athlete.id,
                TrainingBlock.end_date >= horizon_start,
                TrainingBlock.start_date <= horizon_end,
            )):
                db.execute(update(PlannedWorkout).where(
                    PlannedWorkout.athlete_id == athlete.id, PlannedWorkout.block_id == existing.id,
                ).values(block_id=None))
                db.delete(existing)
            db.flush()
        for item in preview:
            block = TrainingBlock(
                athlete_id=athlete.id, objective_id=objective.id, name=item["name"],
                block_type=item["block_type"],
                start_date=date.fromisoformat(item["start_date"]),
                end_date=date.fromisoformat(item["end_date"]),
                targets=item["targets"],
            )
            db.add(block)
            db.flush()
            applied.append({"id": block.id, "name": block.name, "block_type": block.block_type})
        # The race belongs in the plan. Without it the calendar taper just stops and the
        # projection has no load on the day that matters most. Locked, because the date
        # and the distance are not the planner's to move.
        race_at = datetime.combine(objective.event_date, time(8, 0), tzinfo=timezone.utc)
        existing_race = db.scalar(select(PlannedWorkout).where(
            PlannedWorkout.athlete_id == athlete.id,
            PlannedWorkout.objective_id == objective.id,
            PlannedWorkout.scheduled_at >= day_start(objective.event_date),
            PlannedWorkout.scheduled_at <= day_end(objective.event_date),
        ))
        if existing_race is None:
            race_duration = objective.expected_duration_s or (
                (objective.distance_m or 0) / 3.2 if objective.distance_m else 3 * 3600)
            race = PlannedWorkout(
                athlete_id=athlete.id, objective_id=objective.id,
                scheduled_at=race_at, sport=objective.sport, name=objective.name,
                duration_s=race_duration, distance_m=objective.distance_m,
                elevation_m=objective.elevation_m, locked=True,
                steps=[{"type": "race", "intensity": "race", "duration_s": race_duration,
                        "distance_m": objective.distance_m}],
                projected_load=estimate_planned_load(race_duration, None, "race").load,
            )
            db.add(race)
            db.flush()
            applied.append({"id": race.id, "name": race.name, "block_type": "race"})

        db.add(PlanChangeAudit(
            athlete_id=athlete.id, entity_type="season_plan", entity_id=objective.id, action="create",
            before_state=None, after_state={"blocks": preview}, initiated_by="user",
            reason=f"Periodised season plan for {objective.name} on {objective.event_date.isoformat()}",
        ))
        db.commit()

    total_weeks = sum(b["weeks"] for b in preview)
    return {
        "objective": {"id": objective.id, "name": objective.name, "date": objective.event_date.isoformat(),
                      "priority": objective.priority, "sport": objective.sport,
                      "long_objective": _long_objective(objective)},
        "start_date": blocks[0].start.isoformat(),
        "total_weeks": total_weeks,
        "basis": {"typical_weekly_load_8w": state.get("typical_weekly_load_8w"),
                  "typical_weekly_hours_8w": state.get("typical_weekly_hours_8w"),
                  "note": "Targets ramp from your own recent load, capped so the plan never generates a load-spike warning."},
        "blocks": preview,
        "applied": applied,
        "method": "linear_periodisation_v1",
    }


@router.post("/coach/generate-block")
def coach_generate_block(payload: GenerateBlockRequest, db: Session = Depends(get_db)):
    """Generate several weeks of workouts at once, each using its periodised target."""
    athlete = demo_athlete(db)
    context = build_athlete_context(db, athlete)
    today = date.today()
    first = payload.week_start or (today + timedelta(days=(7 - today.weekday()) % 7 or 7))
    first = first - timedelta(days=first.weekday())

    stored_blocks = [{
        "name": b.name, "block_type": b.block_type, "targets": b.targets or {},
    } for b in db.scalars(select(TrainingBlock).where(
        TrainingBlock.athlete_id == athlete.id,
        TrainingBlock.end_date >= first,
        TrainingBlock.start_date <= first + timedelta(weeks=payload.weeks),
    ))]

    seed_summary, seed_commands = generate_block_seed(
        context, first, payload.weeks, stored_blocks, payload.objective_id, payload.strategy)
    if not seed_commands:
        raise HTTPException(409, "Nothing to propose: every day in the range is already planned or unavailable.")

    validation = validate_commands(db, athlete.id, seed_commands)
    proposal = AIProposal(
        athlete_id=athlete.id, proposal_type="generate_block", status="pending",
        title=f"{payload.weeks} weeks from {first.isoformat()}",
        summary=seed_summary, commands=validation.get("commands") or seed_commands,
        context_snapshot=_compact_context(context), validation=validation, provider="local",
    )
    db.add(proposal); db.commit(); db.refresh(proposal)
    return _proposal_dict(proposal)


@router.get("/analytics/block-progress")
def block_progress(db: Session = Depends(get_db)):
    """Target vs planned vs actual, per week, for every block in the horizon.

    The one view where objective, block, plan and completed training meet. Without it a
    block's targets were unverifiable: nothing compared what was prescribed with what
    was scheduled or what actually happened.
    """
    athlete = demo_athlete(db)
    today = date.today()
    blocks = list(db.scalars(select(TrainingBlock).where(
        TrainingBlock.athlete_id == athlete.id,
        TrainingBlock.end_date >= today - timedelta(days=56),
    ).order_by(TrainingBlock.start_date)))
    if not blocks:
        return {"blocks": [], "note": "No training blocks yet. Generate a season plan from an objective."}

    horizon_start = min(b.start_date for b in blocks)
    horizon_end = max(b.end_date for b in blocks)

    planned_by_week: dict[str, float] = {}
    for workout in db.scalars(select(PlannedWorkout).where(
        PlannedWorkout.athlete_id == athlete.id,
        PlannedWorkout.scheduled_at >= day_start(horizon_start),
        PlannedWorkout.scheduled_at <= day_end(horizon_end),
    )):
        day = to_utc(workout.scheduled_at).date()
        monday = (day - timedelta(days=day.weekday())).isoformat()
        planned_by_week[monday] = planned_by_week.get(monday, 0.0) + float(workout.projected_load or 0)

    actual_by_week: dict[str, float] = {}
    for activity, metrics in db.execute(select(Activity, ActivityMetrics).join(ActivityMetrics).where(
        Activity.athlete_id == athlete.id,
        Activity.start_time >= day_start(horizon_start),
        Activity.start_time <= day_end(min(horizon_end, today)),
    )):
        day = to_utc(activity.start_time).date()
        monday = (day - timedelta(days=day.weekday())).isoformat()
        actual_by_week[monday] = actual_by_week.get(monday, 0.0) + float(metrics.training_load or 0)

    out = []
    for block in blocks:
        weeks = []
        for target in ((block.targets or {}).get("week_targets") or []):
            monday = target.get("week_start")
            if not monday:
                continue
            target_load = float(target.get("target_load") or 0)
            planned = round(planned_by_week.get(monday, 0.0), 1)
            actual = round(actual_by_week.get(monday, 0.0), 1)
            weeks.append({
                **target, "planned_load": planned, "actual_load": actual,
                "planned_vs_target_pct": round(planned / target_load * 100) if target_load else None,
                "actual_vs_target_pct": round(actual / target_load * 100) if target_load else None,
                "is_past": date.fromisoformat(monday) < today - timedelta(days=today.weekday()),
            })
        out.append({
            "id": block.id, "name": block.name, "block_type": block.block_type,
            "start_date": block.start_date.isoformat(), "end_date": block.end_date.isoformat(),
            "is_current": block.start_date <= today <= block.end_date,
            "targets": {k: v for k, v in (block.targets or {}).items() if k != "week_targets"},
            "weeks": weeks,
        })
    return {"blocks": out, "as_of": today.isoformat()}

@router.post("/coach/adapt-week")
def coach_adapt_week(payload: AdaptWeekRequest, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    context = build_athlete_context(db, athlete)
    today = date.today()
    week_start = payload.week_start or (today - timedelta(days=today.weekday()))
    week_start = week_start - timedelta(days=week_start.weekday())
    seed_summary, seed_commands = adapt_week_seed(context, week_start)
    provider, provider_error = resolve_provider()
    try:
        if provider_error:
            raise RuntimeError(provider_error)
        plan = provider.refine_plan("adapt_week", context, seed_summary, seed_commands)
        commands, summary, provider_name = plan.commands, plan.summary, plan.provider
    except Exception as exc:
        commands, summary, provider_name = seed_commands, seed_summary + f" Provider refinement was unavailable ({str(exc)[:120]}).", "local_fallback"
    validation = validate_commands(db, athlete.id, commands)
    if not validation["valid"] and commands != seed_commands:
        seed_validation = validate_commands(db, athlete.id, seed_commands)
        if seed_validation["valid"]:
            commands, validation, provider_name = seed_commands, seed_validation, "local_fallback"
            summary += " The remote proposal failed deterministic validation, so the validated local seed was kept."
    proposal = AIProposal(
        athlete_id=athlete.id, proposal_type="adapt_week", status="pending", title=f"Adapt week of {week_start.isoformat()}",
        summary=summary, commands=validation.get("commands") or commands, context_snapshot=_compact_context(context),
        validation=validation, provider=provider_name,
    )
    db.add(proposal); db.commit(); db.refresh(proposal)
    return _proposal_dict(proposal)


@router.get("/coach/proposals")
def coach_proposals(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    rows = db.scalars(select(AIProposal).where(AIProposal.athlete_id == athlete.id).order_by(AIProposal.created_at.desc()).limit(limit))
    return [_proposal_dict(p) for p in rows]


@router.post("/coach/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    proposal = db.get(AIProposal, proposal_id)
    if not proposal or proposal.athlete_id != athlete.id:
        raise HTTPException(404, "AI proposal not found")
    if proposal.status != "pending":
        raise HTTPException(409, f"Proposal is already {proposal.status}")
    validation = validate_commands(db, athlete.id, proposal.commands or [])
    proposal.validation = validation
    if not validation["valid"]:
        db.commit()
        raise HTTPException(409, {"message": "Proposal is no longer valid against the current calendar", "validation": validation})
    try:
        applied = apply_commands(db, athlete.id, proposal.commands or [], proposal.summary)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    proposal.status = "applied"
    proposal.decided_at = utcnow()
    db.commit(); db.refresh(proposal)
    return {"proposal": _proposal_dict(proposal), "applied": applied}


@router.post("/coach/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    proposal = db.get(AIProposal, proposal_id)
    if not proposal or proposal.athlete_id != athlete.id:
        raise HTTPException(404, "AI proposal not found")
    if proposal.status != "pending":
        raise HTTPException(409, f"Proposal is already {proposal.status}")
    proposal.status = "rejected"
    proposal.decided_at = utcnow()
    db.commit(); db.refresh(proposal)
    return _proposal_dict(proposal)


@router.get("/planned-workouts/{workout_id}/why")
def why_planned_workout(workout_id: int, db: Session = Depends(get_db)):
    athlete = demo_athlete(db)
    workout = db.get(PlannedWorkout, workout_id)
    if not workout or workout.athlete_id != athlete.id:
        raise HTTPException(404, "Planned workout not found")
    return explain_workout(db, athlete.id, workout)
