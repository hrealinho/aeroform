from __future__ import annotations
from datetime import date
from pathlib import Path
import hashlib
import os
import shutil
import tempfile
import zipfile
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.core.config import settings
from app.domain.models import Activity, ActivitySource, ActivityStreams, ActivityMetrics, RawActivityFile, ImportSession, AthleteThreshold
from app.importers.common import ParsedActivity
from app.importers.fit import parse_fit
from app.importers.gpx import parse_gpx
from app.importers.tcx import parse_tcx
from app.importers.zip_import import safe_members
from app.metrics.load import power_load, hr_trimp_load, rpe_load, mountain_mechanical_load
from app.metrics.streams import normalized_power, power_zone_seconds, hr_zone_seconds, aerobic_decoupling
from app.services.dedup import activity_fingerprint

PARSERS = {".fit": parse_fit, ".gpx": parse_gpx, ".tcx": parse_tcx}


def sha256_file(path: str):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def threshold(db: Session, athlete_id: int, sport: str, metric: str, at: date):
    stmt = select(AthleteThreshold).where(
        AthleteThreshold.athlete_id == athlete_id,
        AthleteThreshold.sport == sport,
        AthleteThreshold.metric == metric,
        AthleteThreshold.valid_from <= at,
    ).order_by(AthleteThreshold.valid_from.desc())
    for item in db.scalars(stmt):
        if item.valid_until is None or item.valid_until >= at:
            return item.value
    return None


def enrich_stream_metrics(db: Session, athlete_id: int, parsed: ParsedActivity) -> dict:
    day = parsed.start_time.date()
    threshold_power = threshold(db, athlete_id, parsed.sport, "ftp" if parsed.sport == "cycling" else "critical_power", day)
    threshold_hr = threshold(db, athlete_id, parsed.sport, "threshold_hr", day) or threshold(db, athlete_id, "global", "threshold_hr", day)
    computed_np = normalized_power(parsed.streams)
    if parsed.normalized_power is None and computed_np is not None:
        parsed.normalized_power = computed_np
    return {
        "normalized_power_computed": computed_np,
        "power_zones_s": power_zone_seconds(parsed.streams, threshold_power),
        "hr_zones_s": hr_zone_seconds(parsed.streams, threshold_hr),
        "aerobic_decoupling_pct": aerobic_decoupling(parsed.streams),
    }


def calculate_load(db: Session, athlete_id: int, parsed: ParsedActivity):
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


def _fill_missing(activity: Activity, parsed: ParsedActivity) -> None:
    """Merge conservatively: an integration can enrich fields but does not erase richer uploaded data."""
    for attr in ["subtype", "name", "moving_time_s", "distance_m", "elevation_gain_m", "elevation_loss_m", "avg_hr", "max_hr", "avg_power", "normalized_power", "avg_cadence"]:
        current = getattr(activity, attr)
        incoming = getattr(parsed, attr)
        if current is None and incoming is not None:
            setattr(activity, attr, incoming)


def ingest_parsed(
    db: Session,
    athlete_id: int,
    parsed: ParsedActivity,
    source_type: str,
    external_id: str | None = None,
    raw_file_id: int | None = None,
    import_session_id: int | None = None,
):
    # Source IDs are the strongest deduplication key when available.
    source = None
    if external_id:
        source = db.scalar(select(ActivitySource).join(Activity).where(
            Activity.athlete_id == athlete_id,
            ActivitySource.source_type == source_type,
            ActivitySource.external_id == external_id,
        ))
    stream_details = enrich_stream_metrics(db, athlete_id, parsed)
    if source:
        activity = source.activity
        _fill_missing(activity, parsed)
        source.metadata_json = parsed.source_metadata
        if parsed.streams and (activity.streams is None or not activity.streams.samples):
            if activity.streams is None:
                db.add(ActivityStreams(activity_id=activity.id, samples=parsed.streams))
            else:
                activity.streams.samples = parsed.streams
        db.commit()
        return activity, True

    fp = activity_fingerprint(parsed.sport, parsed.start_time, parsed.duration_s, parsed.distance_m)
    existing = db.scalar(select(Activity).where(Activity.athlete_id == athlete_id, Activity.fingerprint == fp))
    if existing:
        _fill_missing(existing, parsed)
        existing_source = db.scalar(select(ActivitySource).where(
            ActivitySource.activity_id == existing.id,
            ActivitySource.source_type == source_type,
            ActivitySource.external_id == external_id if external_id is not None else ActivitySource.raw_file_id == raw_file_id,
        ))
        if not existing_source:
            db.add(ActivitySource(
                activity_id=existing.id,
                source_type=source_type,
                external_id=external_id,
                raw_file_id=raw_file_id,
                import_session_id=import_session_id,
                metadata_json=parsed.source_metadata,
            ))
        if parsed.streams and (existing.streams is None or not existing.streams.samples):
            if existing.streams is None:
                db.add(ActivityStreams(activity_id=existing.id, samples=parsed.streams))
            else:
                existing.streams.samples = parsed.streams
        db.commit()
        return existing, True

    activity = Activity(
        athlete_id=athlete_id,
        sport=parsed.sport,
        subtype=parsed.subtype,
        name=parsed.name,
        start_time=parsed.start_time,
        duration_s=parsed.duration_s,
        moving_time_s=parsed.moving_time_s,
        distance_m=parsed.distance_m,
        elevation_gain_m=parsed.elevation_gain_m,
        elevation_loss_m=parsed.elevation_loss_m,
        avg_hr=parsed.avg_hr,
        max_hr=parsed.max_hr,
        avg_power=parsed.avg_power,
        normalized_power=parsed.normalized_power,
        avg_cadence=parsed.avg_cadence,
        fingerprint=fp,
    )
    db.add(activity)
    db.flush()
    db.add(ActivitySource(
        activity_id=activity.id,
        source_type=source_type,
        external_id=external_id,
        raw_file_id=raw_file_id,
        import_session_id=import_session_id,
        metadata_json=parsed.source_metadata,
    ))
    if parsed.streams:
        db.add(ActivityStreams(activity_id=activity.id, samples=parsed.streams))
    result, mech = calculate_load(db, athlete_id, parsed)
    details = dict(result.details)
    details.update({k: v for k, v in stream_details.items() if v is not None})
    db.add(ActivityMetrics(
        activity_id=activity.id,
        training_load=result.load,
        load_method=result.method,
        load_confidence=result.confidence,
        intensity_factor=result.intensity_factor,
        trimp=result.trimp,
        mechanical_load=mech,
        metric_version=settings.metric_version,
        details=details,
    ))
    db.commit()
    db.refresh(activity)
    return activity, False


def ingest_file(db: Session, athlete_id: int, path: str, source_type: str = "upload", import_session_id: int | None = None):
    suffix = Path(path).suffix.lower()
    if suffix not in PARSERS:
        raise ValueError(f"Unsupported activity format: {suffix}")
    digest = sha256_file(path)
    os.makedirs(settings.storage_path, exist_ok=True)
    storage_key = os.path.join(settings.storage_path, digest + suffix)
    if not os.path.exists(storage_key):
        shutil.copy2(path, storage_key)
    raw = db.scalar(select(RawActivityFile).where(RawActivityFile.storage_key == storage_key))
    if raw is None:
        raw = RawActivityFile(
            athlete_id=athlete_id,
            import_session_id=import_session_id,
            filename=Path(path).name,
            storage_key=storage_key,
            sha256=digest,
            parser_version=settings.metric_version,
        )
        db.add(raw)
        db.flush()
    parsed = PARSERS[suffix](path)
    return ingest_parsed(db, athlete_id, parsed, source_type, raw_file_id=raw.id, import_session_id=import_session_id)


def ingest_zip(db: Session, athlete_id: int, zip_path: str, import_session: ImportSession, finalize: bool = True):
    members = list(safe_members(zip_path))
    import_session.discovered_count += max(0, len(members) - 1)
    import_session.status = "processing"
    db.commit()
    errors = []
    with zipfile.ZipFile(zip_path) as zf, tempfile.TemporaryDirectory() as tmp:
        for index, info in enumerate(members):
            try:
                suffix = Path(info.filename).suffix.lower()
                target = os.path.join(tmp, f"{index}{suffix}")
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                _, duplicate = ingest_file(db, athlete_id, target, "zip", import_session.id)
                if duplicate:
                    import_session.duplicate_count += 1
                else:
                    import_session.imported_count += 1
            except Exception as exc:
                import_session.failed_count += 1
                errors.append({"file": info.filename, "error": str(exc)[:500]})
            finally:
                db.commit()
    if finalize:
        import_session.status = "completed_with_errors" if errors else "completed"
        import_session.error_summary = {"errors": errors[:100]} if errors else None
    elif errors:
        current = (import_session.error_summary or {}).get("errors", [])
        import_session.error_summary = {"errors": (current + errors)[:100]}
    db.commit()
