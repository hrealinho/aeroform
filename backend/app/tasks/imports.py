from datetime import timedelta
from pathlib import Path
import shutil

from sqlalchemy import select

from app.core.timeutil import utcnow
from app.tasks.celery_app import celery_app
from app.db.session import SessionLocal
from app.domain.models import Activity, ImportSession
from app.services.ingestion import ingest_file, ingest_zip, recalculate_activity_metrics
from app.integrations.strava.sync import historical_sync, sync_activity, delete_activity_source


@celery_app.task(bind=True, autoretry_for=(OSError,), retry_backoff=True, max_retries=3)
def process_uploaded_files(self, athlete_id: int, import_session_id: int, paths: list[str], source_type: str = "upload"):
    db = SessionLocal()
    try:
        session = db.get(ImportSession, import_session_id)
        if not session:
            return {"error": "import session not found"}
        session.status = "processing"
        db.commit()
        errors = []
        for path in paths:
            try:
                if Path(path).suffix.lower() == ".zip":
                    before_failures = session.failed_count
                    ingest_zip(db, athlete_id, path, session, finalize=False)
                    if session.failed_count > before_failures and session.error_summary:
                        errors.extend(session.error_summary.get("errors", []))
                else:
                    _, duplicate = ingest_file(db, athlete_id, path, source_type, session.id)
                    session.duplicate_count += int(duplicate)
                    session.imported_count += int(not duplicate)
                    db.commit()
            except Exception as exc:
                session.failed_count += 1
                errors.append({"file": Path(path).name, "error": str(exc)[:500]})
                db.commit()
        session.status = "completed_with_errors" if session.failed_count else "completed"
        session.error_summary = {"errors": errors[:100]} if errors else session.error_summary
        db.commit()
        return {"import_session_id": import_session_id, "status": session.status}
    finally:
        db.close()
        # Raw files now live under the content-addressed storage path, so the upload
        # staging directory is dead weight. Nothing else cleaned it up, so every upload
        # leaked a copy of its files forever.
        _cleanup_staging(paths)


def _cleanup_staging(paths: list[str]) -> None:
    for parent in {Path(p).parent for p in paths}:
        try:
            if parent.name and parent.parent.name == "staging" and parent.exists():
                shutil.rmtree(parent, ignore_errors=True)
        except OSError:
            pass


@celery_app.task(bind=True, autoretry_for=(OSError,), retry_backoff=True, max_retries=3)
def sync_strava_history(self, athlete_id: int, import_session_id: int):
    db = SessionLocal()
    try:
        result = historical_sync(db, athlete_id, import_session_id)
        if result.get("paused_rate_limit"):
            raise self.retry(countdown=15 * 60, max_retries=96)
        return result
    finally:
        db.close()


@celery_app.task(bind=True, autoretry_for=(OSError,), retry_backoff=True, max_retries=3)
def sync_strava_activity(self, athlete_id: int, strava_activity_id: int):
    db = SessionLocal()
    try:
        activity_id, duplicate = sync_activity(db, athlete_id, strava_activity_id)
        return {"activity_id": activity_id, "duplicate": duplicate}
    finally:
        db.close()


@celery_app.task
def remove_strava_activity(athlete_id: int, strava_activity_id: int):
    db = SessionLocal()
    try:
        return {"deleted": delete_activity_source(db, athlete_id, strava_activity_id)}
    finally:
        db.close()


@celery_app.task(bind=True)
def recompute_athlete_metrics(self, athlete_id: int, since_days: int | None = None):
    """Recompute stored metrics for an athlete's activities.

    Used after a threshold changes: load method selection depends on which thresholds
    were valid on each activity's date, so entering an FTP or resting HR only changes
    historical load once the affected activities are recalculated. This runs as a task
    because a multi-year history is far too slow to do inside a request.
    """
    db = SessionLocal()
    try:
        stmt = select(Activity).where(Activity.athlete_id == athlete_id)
        if since_days:
            stmt = stmt.where(Activity.start_time >= utcnow() - timedelta(days=since_days))
        updated = failed = 0
        for activity in db.scalars(stmt.order_by(Activity.id)):
            try:
                recalculate_activity_metrics(db, athlete_id, activity)
                db.commit()
                updated += 1
            except Exception:
                db.rollback()
                failed += 1
        return {"athlete_id": athlete_id, "recomputed": updated, "failed": failed}
    finally:
        db.close()
