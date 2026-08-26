from __future__ import annotations
from datetime import date
from pathlib import Path
import hashlib, os, shutil, tempfile, zipfile
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.core.config import settings
from app.domain.models import Activity, ActivitySource, ActivityStreams, ActivityMetrics, RawActivityFile, ImportSession, AthleteThreshold
from app.importers.fit import parse_fit
from app.importers.gpx import parse_gpx
from app.importers.tcx import parse_tcx
from app.importers.zip_import import safe_members
from app.metrics.load import power_load, hr_trimp_load, rpe_load, mountain_mechanical_load
from app.services.dedup import activity_fingerprint

PARSERS = {".fit": parse_fit, ".gpx": parse_gpx, ".tcx": parse_tcx}


def sha256_file(path: str):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def threshold(db: Session, athlete_id: int, sport: str, metric: str, at: date):
    stmt = select(AthleteThreshold).where(AthleteThreshold.athlete_id == athlete_id, AthleteThreshold.sport == sport, AthleteThreshold.metric == metric, AthleteThreshold.valid_from <= at).order_by(AthleteThreshold.valid_from.desc())
    for item in db.scalars(stmt):
        if item.valid_until is None or item.valid_until >= at:
            return item.value
    return None


def calculate_load(db: Session, athlete_id: int, parsed):
    day = parsed.start_time.date()
    threshold_power = threshold(db, athlete_id, parsed.sport, "ftp" if parsed.sport == "cycling" else "critical_power", day)
    resting_hr = threshold(db, athlete_id, parsed.sport, "resting_hr", day) or threshold(db, athlete_id, "global", "resting_hr", day)
    max_hr = threshold(db, athlete_id, parsed.sport, "max_hr", day) or threshold(db, athlete_id, "global", "max_hr", day)
    try:
        if parsed.normalized_power and threshold_power:
            result = power_load(parsed.duration_s, parsed.normalized_power, threshold_power)
        elif parsed.avg_power and threshold_power:
            result = power_load(parsed.duration_s, parsed.avg_power, threshold_power)
        elif parsed.avg_hr and resting_hr and max_hr:
            result = hr_trimp_load(parsed.duration_s, parsed.avg_hr, resting_hr, max_hr)
        else:
            # Neutral duration fallback until athlete adds RPE/thresholds.
            result = rpe_load(parsed.duration_s, 3.0)
    except ValueError:
        result = rpe_load(max(parsed.duration_s, 60), 3.0)
    mech = mountain_mechanical_load(parsed.duration_s, parsed.elevation_gain_m, parsed.elevation_loss_m) if parsed.sport in {"running", "trail_running", "hiking", "mountaineering"} else None
    return result, mech


def ingest_file(db: Session, athlete_id: int, path: str, source_type: str = "upload", import_session_id: int | None = None):
    suffix = Path(path).suffix.lower()
    if suffix not in PARSERS:
        raise ValueError(f"Unsupported activity format: {suffix}")
    digest = sha256_file(path)
    os.makedirs(settings.storage_path, exist_ok=True)
    storage_key = os.path.join(settings.storage_path, digest + suffix)
    if not os.path.exists(storage_key): shutil.copy2(path, storage_key)
    raw = db.scalar(select(RawActivityFile).where(RawActivityFile.storage_key == storage_key))
    if raw is None:
        raw = RawActivityFile(athlete_id=athlete_id, import_session_id=import_session_id, filename=Path(path).name, storage_key=storage_key, sha256=digest)
        db.add(raw); db.flush()
    parsed = PARSERS[suffix](path)
    fp = activity_fingerprint(parsed.sport, parsed.start_time, parsed.duration_s, parsed.distance_m)
    existing = db.scalar(select(Activity).where(Activity.athlete_id == athlete_id, Activity.fingerprint == fp))
    if existing:
        if not db.scalar(select(ActivitySource).where(ActivitySource.activity_id == existing.id, ActivitySource.raw_file_id == raw.id)):
            db.add(ActivitySource(activity_id=existing.id, source_type=source_type, raw_file_id=raw.id, import_session_id=import_session_id, metadata_json=parsed.source_metadata))
        db.commit()
        return existing, True
    activity = Activity(athlete_id=athlete_id, sport=parsed.sport, subtype=parsed.subtype, name=parsed.name, start_time=parsed.start_time, duration_s=parsed.duration_s, moving_time_s=parsed.moving_time_s, distance_m=parsed.distance_m, elevation_gain_m=parsed.elevation_gain_m, elevation_loss_m=parsed.elevation_loss_m, avg_hr=parsed.avg_hr, max_hr=parsed.max_hr, avg_power=parsed.avg_power, normalized_power=parsed.normalized_power, avg_cadence=parsed.avg_cadence, fingerprint=fp)
    db.add(activity); db.flush()
    db.add(ActivitySource(activity_id=activity.id, source_type=source_type, raw_file_id=raw.id, import_session_id=import_session_id, metadata_json=parsed.source_metadata))
    db.add(ActivityStreams(activity_id=activity.id, samples=parsed.streams))
    result, mech = calculate_load(db, athlete_id, parsed)
    db.add(ActivityMetrics(activity_id=activity.id, training_load=result.load, load_method=result.method, load_confidence=result.confidence, intensity_factor=result.intensity_factor, trimp=result.trimp, mechanical_load=mech, metric_version=settings.metric_version, details=result.details))
    db.commit(); db.refresh(activity)
    return activity, False


def ingest_zip(db: Session, athlete_id: int, zip_path: str, import_session: ImportSession):
    members = list(safe_members(zip_path))
    import_session.discovered_count = len(members); import_session.status = "processing"; db.commit()
    errors = []
    with zipfile.ZipFile(zip_path) as zf, tempfile.TemporaryDirectory() as tmp:
        for info in members:
            try:
                target = os.path.join(tmp, Path(info.filename).name)
                with zf.open(info) as src, open(target, "wb") as dst: shutil.copyfileobj(src, dst)
                _, duplicate = ingest_file(db, athlete_id, target, "zip", import_session.id)
                if duplicate: import_session.duplicate_count += 1
                else: import_session.imported_count += 1
            except Exception as exc:
                import_session.failed_count += 1
                errors.append({"file": info.filename, "error": str(exc)[:500]})
            finally:
                db.commit()
    import_session.status = "completed_with_errors" if errors else "completed"
    import_session.error_summary = {"errors": errors[:100]} if errors else None
    db.commit()
