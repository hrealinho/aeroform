from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.core.config import settings
from app.domain.models import Activity, ActivitySource, ImportSession, IntegrationConnection
from app.integrations.strava.client import api_get
from app.integrations.strava.mapper import map_activity
from app.services.ingestion import ingest_parsed


def _connection(db: Session, athlete_id: int) -> IntegrationConnection:
    connection = db.scalar(select(IntegrationConnection).where(
        IntegrationConnection.athlete_id == athlete_id,
        IntegrationConnection.provider == "strava",
    ))
    if not connection:
        raise ValueError("Strava is not connected")
    return connection


def _rate_limit_near(headers) -> bool:
    """Stop before exhausting Strava's short-window quota.

    Header format is typically "short,daily". We only use this as a defensive signal;
    absence or malformed headers simply means continue.
    """
    try:
        usage = [int(v) for v in headers.get("X-RateLimit-Usage", "").split(",")]
        limit = [int(v) for v in headers.get("X-RateLimit-Limit", "").split(",")]
        return len(usage) >= 1 and len(limit) >= 1 and usage[0] >= max(1, limit[0] - 2)
    except (TypeError, ValueError):
        return False


def sync_activity(db: Session, athlete_id: int, strava_activity_id: int, import_session_id: int | None = None) -> tuple[int, bool]:
    connection = _connection(db, athlete_id)
    activity_data, _ = api_get(db, connection, f"/activities/{strava_activity_id}")
    streams = None
    if settings.strava_sync_streams:
        try:
            streams, _ = api_get(db, connection, f"/activities/{strava_activity_id}/streams", params={
                "keys": "time,latlng,distance,altitude,velocity_smooth,heartrate,cadence,watts,temp,grade_smooth",
                "key_by_type": "true",
            })
        except Exception:
            # Streams are enrichment, not a reason to lose the activity.
            streams = None
    parsed = map_activity(activity_data, streams if isinstance(streams, dict) else None)
    activity, duplicate = ingest_parsed(
        db,
        athlete_id,
        parsed,
        source_type="strava",
        external_id=str(strava_activity_id),
        import_session_id=import_session_id,
    )
    return activity.id, duplicate


def delete_activity_source(db: Session, athlete_id: int, strava_activity_id: int) -> bool:
    source = db.scalar(select(ActivitySource).join(Activity).where(
        Activity.athlete_id == athlete_id,
        ActivitySource.source_type == "strava",
        ActivitySource.external_id == str(strava_activity_id),
    ))
    if not source:
        return False
    activity_id = source.activity_id
    db.delete(source)
    db.flush()
    remaining = db.scalar(select(func.count(ActivitySource.id)).where(ActivitySource.activity_id == activity_id)) or 0
    if remaining == 0:
        activity = db.get(Activity, activity_id)
        if activity:
            db.delete(activity)
    db.commit()
    return True


def historical_sync(db: Session, athlete_id: int, import_session_id: int, max_pages: int | None = None) -> dict:
    """Backfill history efficiently using paginated summary activities.

    We deliberately do *not* fetch every historical activity individually. That would turn a
    multi-year import into thousands of API requests. Detailed activity/stream fetching is used for
    webhook updates and can later be selectively enriched in a separate low-priority job.
    """
    connection = _connection(db, athlete_id)
    session = db.get(ImportSession, import_session_id)
    if not session:
        raise ValueError("Import session not found")
    state = session.error_summary or {}
    page = int(state.get("next_page", 1))
    failures: list[dict] = list(state.get("errors", []))
    session.status = "processing"
    db.commit()
    pages_this_run = 0
    processed = 0

    while True:
        if max_pages and pages_this_run >= max_pages:
            break
        items, headers = api_get(db, connection, "/athlete/activities", params={"page": page, "per_page": settings.strava_sync_page_size})
        if not isinstance(items, list) or not items:
            session.status = "completed_with_errors" if failures else "completed"
            session.error_summary = {"errors": failures[:100]} if failures else None
            connection.last_sync_at = datetime.now(timezone.utc)
            connection.status = "connected" if not failures else "connected_with_errors"
            connection.last_error = failures[0]["error"] if failures else None
            db.commit()
            return {"processed": processed, "pages": pages_this_run, "failed": len(failures), "completed": True}

        session.discovered_count += len(items)
        db.commit()
        for summary in items:
            try:
                parsed = map_activity(summary)
                _, duplicate = ingest_parsed(
                    db,
                    athlete_id,
                    parsed,
                    source_type="strava",
                    external_id=str(summary["id"]),
                    import_session_id=import_session_id,
                )
                if duplicate:
                    session.duplicate_count += 1
                else:
                    session.imported_count += 1
                processed += 1
            except Exception as exc:
                session.failed_count += 1
                failures.append({"strava_id": summary.get("id"), "error": str(exc)[:400]})
            db.commit()

        pages_this_run += 1
        page += 1
        session.error_summary = {"next_page": page, "errors": failures[:100]}
        db.commit()

        if len(items) < settings.strava_sync_page_size:
            session.status = "completed_with_errors" if failures else "completed"
            session.error_summary = {"errors": failures[:100]} if failures else None
            connection.last_sync_at = datetime.now(timezone.utc)
            connection.status = "connected" if not failures else "connected_with_errors"
            connection.last_error = failures[0]["error"] if failures else None
            db.commit()
            return {"processed": processed, "pages": pages_this_run, "failed": len(failures), "completed": True}

        if _rate_limit_near(headers):
            session.status = "paused_rate_limit"
            db.commit()
            return {"processed": processed, "pages": pages_this_run, "failed": len(failures), "completed": False, "paused_rate_limit": True}

    return {"processed": processed, "pages": pages_this_run, "failed": len(failures), "completed": False}
