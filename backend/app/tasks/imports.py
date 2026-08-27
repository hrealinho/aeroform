from pathlib import Path
from app.tasks.celery_app import celery_app
from app.db.session import SessionLocal
from app.domain.models import ImportSession
from app.services.ingestion import ingest_file, ingest_zip
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
