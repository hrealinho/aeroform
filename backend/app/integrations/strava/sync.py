from __future__ import annotations
from datetime import timedelta

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.timeutil import ensure_aware, utcnow
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



# The resume cursor and the error log share ImportSession.error_summary because there is
# no migration tooling yet. They are kept under separate, clearly named keys so the
# cursor is never mistaken for an error and is never dropped when the errors are
# rewritten - which previously made a resumed backfill restart from page 1.
def _read_state(session: ImportSession) -> tuple[int, list[dict], int]:
    state = session.error_summary if isinstance(session.error_summary, dict) else {}
    sync = state.get("sync") if isinstance(state.get("sync"), dict) else {}
    # Fall back to the old top-level key so an import queued before this change resumes.
    next_page = int(sync.get("next_page") or state.get("next_page") or 1)
    counted_pages = int(sync.get("counted_pages") or 0)
    errors = list(state.get("errors") or [])
    return next_page, errors, counted_pages


def _write_state(session: ImportSession, next_page: int, errors: list[dict], counted_pages: int, completed: bool) -> None:
    summary: dict = {}
    if errors:
        summary["errors"] = errors[:100]
    # Keep the cursor after completion too: it records where the backfill finished and
    # makes an interrupted-then-resumed run auditable.
    summary["sync"] = {"next_page": next_page, "counted_pages": counted_pages, "completed": completed}
    session.error_summary = summary



# A sync that dies mid-run leaves its session in `processing` forever, and the
# already-running guard then refuses to start a new one. Anything untouched for this long
# is treated as abandoned rather than blocking the athlete indefinitely.
STALE_IMPORT_AFTER_HOURS = 6


def reap_stale_sessions(db: Session, athlete_id: int, source: str = "strava") -> int:
    """Mark abandoned in-flight import sessions as failed. Returns how many were reaped."""
    cutoff = utcnow() - timedelta(hours=STALE_IMPORT_AFTER_HOURS)
    stale = list(db.scalars(select(ImportSession).where(
        ImportSession.athlete_id == athlete_id,
        ImportSession.source == source,
        ImportSession.status.in_(["queued", "processing"]),
    )))
    reaped = 0
    for session in stale:
        updated = session.updated_at or session.created_at
        if updated is not None and ensure_aware(updated) > cutoff:
            continue
        session.status = "failed"
        state = session.error_summary if isinstance(session.error_summary, dict) else {}
        errors = list(state.get("errors") or [])
        errors.append({"error": f"Import abandoned: no progress for over {STALE_IMPORT_AFTER_HOURS}h."})
        state["errors"] = errors[:100]
        session.error_summary = state
        reaped += 1
    if reaped:
        db.commit()
    return reaped

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


def historical_sync(db: Session, athlete_id: int, import_session_id: int, max_pages: int | None = None,
                    after_days: int | None = None) -> dict:
    """Backfill history efficiently using paginated summary activities.

    We deliberately do *not* fetch every historical activity individually. That would turn a
    multi-year import into thousands of API requests. Detailed activity/stream fetching is used for
    webhook updates and can later be selectively enriched in a separate low-priority job.
    """
    connection = _connection(db, athlete_id)
    session = db.get(ImportSession, import_session_id)
    if not session:
        raise ValueError("Import session not found")
    page, failures, counted_pages = _read_state(session)
    session.status = "processing"
    db.commit()
    pages_this_run = 0
    processed = 0

    while True:
        if max_pages and pages_this_run >= max_pages:
            break
        params = {"page": page, "per_page": settings.strava_sync_page_size}
        if after_days:
            # Scoping the window matters when streams are enabled: each activity costs an
            # extra API call, and Strava allows ~100 per 15 minutes.
            params["after"] = int((utcnow() - timedelta(days=after_days)).timestamp())
        items, headers = api_get(db, connection, "/athlete/activities", params=params)
        if not isinstance(items, list) or not items:
            session.status = "completed_with_errors" if failures else "completed"
            _write_state(session, page, failures, counted_pages, completed=True)
            connection.last_sync_at = utcnow()
            connection.status = "connected" if not failures else "connected_with_errors"
            connection.last_error = failures[0]["error"] if failures else None
            db.commit()
            return {"processed": processed, "pages": pages_this_run, "failed": len(failures), "completed": True}

        if page > counted_pages:
            # Only count a page the first time it is seen. A resumed or retried run used
            # to add its pages again, inflating discovered_count past the real total.
            session.discovered_count += len(items)
            counted_pages = page
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
                # A failed flush poisons the SQLAlchemy transaction until rollback.
                # Concurrent backfills can race on the fingerprint unique constraint;
                # after rollback, retry once so the winner is found by the normal
                # deduplication path and counted as a duplicate rather than a failure.
                db.rollback()
                session = db.get(ImportSession, import_session_id)
                connection = _connection(db, athlete_id)
                if isinstance(exc, IntegrityError):
                    try:
                        _, duplicate = ingest_parsed(
                            db, athlete_id, parsed, source_type="strava",
                            external_id=str(summary["id"]), import_session_id=import_session_id,
                        )
                        # A successful retry is a success whether or not it deduplicated.
                        # Without this branch a retry that imported a new activity fell
                        # through to failed_count, which is why a completed backfill could
                        # report every one of its activities as failed while the rows were
                        # sitting in the database.
                        session = db.get(ImportSession, import_session_id)
                        if duplicate:
                            session.duplicate_count += 1
                        else:
                            session.imported_count += 1
                        processed += 1
                        db.commit()
                        continue
                    except Exception as retry_exc:
                        db.rollback()
                        session = db.get(ImportSession, import_session_id)
                        connection = _connection(db, athlete_id)
                        exc = retry_exc
                session.failed_count += 1
                failures.append({"strava_id": summary.get("id"), "error": str(exc)[:400]})
            db.commit()

        pages_this_run += 1
        page += 1
        _write_state(session, page, failures, counted_pages, completed=False)
        db.commit()

        if len(items) < settings.strava_sync_page_size:
            session.status = "completed_with_errors" if failures else "completed"
            _write_state(session, page, failures, counted_pages, completed=True)
            connection.last_sync_at = utcnow()
            connection.status = "connected" if not failures else "connected_with_errors"
            connection.last_error = failures[0]["error"] if failures else None
            db.commit()
            return {"processed": processed, "pages": pages_this_run, "failed": len(failures), "completed": True}

        if _rate_limit_near(headers):
            session.status = "paused_rate_limit"
            db.commit()
            return {"processed": processed, "pages": pages_this_run, "failed": len(failures), "completed": False, "paused_rate_limit": True}

    return {"processed": processed, "pages": pages_this_run, "failed": len(failures), "completed": False}
