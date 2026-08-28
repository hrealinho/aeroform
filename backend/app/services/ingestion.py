from __future__ import annotations
from datetime import date
from pathlib import Path
import hashlib
import gzip
import os
import shutil
import tempfile
import zipfile
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.core.config import settings
from app.core.timeutil import to_utc
from app.domain.models import Activity, ActivitySource, ActivityStreams, ActivityMetrics, RawActivityFile, ImportSession, AthleteThreshold
from app.importers.common import ParsedActivity
from app.importers.fit import parse_fit
from app.importers.gpx import parse_gpx
from app.importers.tcx import parse_tcx
from app.importers.zip_import import extract_member, safe_members
from app.importers.formats import activity_format, safe_basename
from app.metrics.load import power_load, hr_trimp_load, rpe_load, terrain_load_profile, composite_training_load, resolve_durations, LoadResult
from app.metrics.streams import normalized_power, power_zone_seconds, hr_zone_seconds, aerobic_decoupling
from app.metrics.power_profile import mean_maximal
from app.metrics.terrain import terrain_stream_metrics
from app.services.classification import apply_classification
from app.services.dedup import activity_fingerprint

PARSERS = {".fit": parse_fit, ".gpx": parse_gpx, ".tcx": parse_tcx}


MAX_GZIP_EXPANDED_BYTES = 500 * 1024 * 1024


def _decompress_gzip_limited(source: str, target: str, max_bytes: int = MAX_GZIP_EXPANDED_BYTES) -> None:
    written = 0
    with gzip.open(source, "rb") as src, open(target, "wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise ValueError("Gzip activity exceeds expanded size limit")
            dst.write(chunk)


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
    )
    items = [item for item in db.scalars(stmt) if item.valid_until is None or item.valid_until >= at]
    items.sort(key=lambda item: (item.valid_from, 1 if item.source == "manual" else 0), reverse=True)
    return items[0].value if items else None


def enrich_stream_metrics(db: Session, athlete_id: int, parsed: ParsedActivity) -> dict:
    day = to_utc(parsed.start_time).date()
    threshold_power = threshold(db, athlete_id, parsed.sport, "ftp" if parsed.sport == "cycling" else "critical_power", day)
    threshold_hr = threshold(db, athlete_id, parsed.sport, "threshold_hr", day) or threshold(db, athlete_id, "global", "threshold_hr", day)
    computed_np = normalized_power(parsed.streams)
    if parsed.normalized_power is None and computed_np is not None:
        parsed.normalized_power = computed_np
    terrain_stream = terrain_stream_metrics(parsed.streams, parsed.duration_s)
    # Device-provided FIT totals remain authoritative. Stream-derived gain/loss only
    # fill gaps, notably GPX/Strava sources that do not expose descent summaries.
    if parsed.elevation_gain_m is None and terrain_stream.get("stream_elevation_gain_m") is not None:
        parsed.elevation_gain_m = terrain_stream["stream_elevation_gain_m"]
    if parsed.elevation_loss_m is None and terrain_stream.get("stream_elevation_loss_m") is not None:
        parsed.elevation_loss_m = terrain_stream["stream_elevation_loss_m"]
    if parsed.moving_time_s is None and terrain_stream.get("stream_moving_time_s"):
        parsed.moving_time_s = terrain_stream["stream_moving_time_s"]
    return {
        "normalized_power_computed": computed_np,
        "power_zones_s": power_zone_seconds(parsed.streams, threshold_power, parsed.duration_s),
        "hr_zones_s": hr_zone_seconds(parsed.streams, threshold_hr, parsed.duration_s),
        "aerobic_decoupling_pct": aerobic_decoupling(parsed.streams),
        # Per-activity bests are stored so a multi-year power curve is an aggregation
        # rather than a re-read of every stream on every request.
        "mean_max_power": mean_maximal(parsed.streams, "power") or None,
        "mean_max_speed": mean_maximal(parsed.streams, "speed") or None,
        **{k: v for k, v in terrain_stream.items() if v is not None},
    }


def calculate_load(db: Session, athlete_id: int, parsed: ParsedActivity):
    """Return the v0.5 composite load plus transparent component profile."""
    day = to_utc(parsed.start_time).date()
    threshold_power = threshold(db, athlete_id, parsed.sport, "ftp" if parsed.sport == "cycling" else "critical_power", day)
    resting_hr = threshold(db, athlete_id, parsed.sport, "resting_hr", day) or threshold(db, athlete_id, "global", "resting_hr", day)
    max_hr = threshold(db, athlete_id, parsed.sport, "max_hr", day) or threshold(db, athlete_id, "global", "max_hr", day)
    # Power/HR averages describe the moving window, so metabolic load is calculated
    # against moving time. Time-on-feet keeps elapsed time, clamped for plausibility.
    durations = resolve_durations(parsed.duration_s, parsed.moving_time_s)
    try:
        if parsed.normalized_power and threshold_power:
            metabolic = power_load(durations.metabolic_s, parsed.normalized_power, threshold_power)
        elif parsed.avg_power and threshold_power:
            metabolic = power_load(durations.metabolic_s, parsed.avg_power, threshold_power)
        elif parsed.avg_hr and resting_hr and max_hr:
            metabolic = hr_trimp_load(durations.metabolic_s, parsed.avg_hr, resting_hr, max_hr)
        else:
            # Neutral duration fallback until athlete adds RPE/thresholds.
            metabolic = rpe_load(durations.metabolic_s, parsed.rpe or 3.0)
    except ValueError:
        metabolic = rpe_load(max(durations.metabolic_s, 60), parsed.rpe or 3.0)

    terrain = terrain_load_profile(
        parsed.sport, durations.durability_s, parsed.distance_m, parsed.elevation_gain_m, parsed.elevation_loss_m
    )
    overall, blend = composite_training_load(parsed.sport, metabolic.load, terrain)
    details = {
        **metabolic.details,
        "metabolic_load": metabolic.load,
        "metabolic_method": metabolic.method,
        "durations": durations.as_dict(),
        "terrain": terrain.as_dict() if terrain else None,
        "composite": blend,
    }
    hybrid = terrain is not None and parsed.sport in {"running", "trail_running", "hiking", "mountaineering"}
    result = LoadResult(
        load=overall,
        method=f"hybrid_{metabolic.method}_terrain" if hybrid else metabolic.method,
        confidence=metabolic.confidence,
        details=details,
        intensity_factor=metabolic.intensity_factor,
        trimp=metabolic.trimp,
        mechanical_load=terrain.mechanical_load if terrain else None,
    )
    return result, terrain



def parsed_from_activity(activity: Activity, source_metadata: dict | None = None) -> ParsedActivity:
    samples = activity.streams.samples if activity.streams is not None and activity.streams.samples else []
    return ParsedActivity(
        sport=activity.sport,
        subtype=activity.subtype,
        name=activity.name,
        start_time=activity.start_time,
        duration_s=activity.duration_s,
        moving_time_s=activity.moving_time_s,
        distance_m=activity.distance_m,
        elevation_gain_m=activity.elevation_gain_m,
        elevation_loss_m=activity.elevation_loss_m,
        avg_hr=activity.avg_hr,
        max_hr=activity.max_hr,
        avg_power=activity.avg_power,
        normalized_power=activity.normalized_power,
        avg_cadence=activity.avg_cadence,
        rpe=activity.rpe,
        streams=samples,
        source_metadata=source_metadata or {},
    )


def write_metrics(db: Session, athlete_id: int, activity: Activity, parsed: ParsedActivity, stream_details: dict | None = None) -> ActivityMetrics:
    # Callers that already ran enrich_stream_metrics pass the result in. Recomputing it
    # here meant normalized power, both zone maps, decoupling and terrain were derived
    # twice over the full stream on every re-sync.
    stream_details = stream_details if stream_details is not None else enrich_stream_metrics(db, athlete_id, parsed)
    # Persist any high-quality fields derived from streams.
    if activity.normalized_power is None and parsed.normalized_power is not None:
        activity.normalized_power = parsed.normalized_power
    if activity.elevation_gain_m is None and parsed.elevation_gain_m is not None:
        activity.elevation_gain_m = parsed.elevation_gain_m
    if activity.elevation_loss_m is None and parsed.elevation_loss_m is not None:
        activity.elevation_loss_m = parsed.elevation_loss_m

    result, terrain = calculate_load(db, athlete_id, parsed)
    details = dict(result.details)
    details.update({k: v for k, v in stream_details.items() if v is not None})
    if parsed.source_metadata.get("classification"):
        details["classification"] = parsed.source_metadata["classification"]
    metrics = activity.metrics
    if metrics is None:
        metrics = ActivityMetrics(activity_id=activity.id)
        db.add(metrics)
    metrics.training_load = result.load
    metrics.load_method = result.method
    metrics.load_confidence = result.confidence
    metrics.intensity_factor = result.intensity_factor
    metrics.trimp = result.trimp
    metrics.mechanical_load = terrain.mechanical_load if terrain else None
    metrics.metric_version = settings.metric_version
    metrics.details = details
    return metrics


def recalculate_activity_metrics(db: Session, athlete_id: int, activity: Activity, classification_metadata: dict | None = None) -> ActivityMetrics:
    parsed = parsed_from_activity(activity, classification_metadata)
    return write_metrics(db, athlete_id, activity, parsed)

def _fill_missing(activity: Activity, parsed: ParsedActivity) -> None:
    """Merge conservatively: an integration can enrich fields but does not erase richer uploaded data."""
    for attr in ["subtype", "name", "moving_time_s", "distance_m", "elevation_gain_m", "elevation_loss_m", "avg_hr", "max_hr", "avg_power", "normalized_power", "avg_cadence", "rpe"]:
        current = getattr(activity, attr)
        incoming = getattr(parsed, attr)
        if current is None and incoming is not None:
            setattr(activity, attr, incoming)



def _reusable_stream_details(parsed: ParsedActivity, merged: ParsedActivity, stream_details: dict) -> dict | None:
    """Reuse already-computed stream metrics when the merged activity has the same stream.

    Returning None tells write_metrics to recompute, which is only necessary when the
    stored activity carries a different (usually richer) stream than the incoming file.
    """
    if merged.streams is parsed.streams or merged.streams == parsed.streams:
        return stream_details
    return None


def ingest_parsed(
    db: Session,
    athlete_id: int,
    parsed: ParsedActivity,
    source_type: str,
    external_id: str | None = None,
    raw_file_id: int | None = None,
    import_session_id: int | None = None,
):
    # Normalize sport before deduplication so a trail run and road run do not share
    # an accidental fingerprint solely because a source used a generic sport label.
    apply_classification(parsed)
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
                activity.streams = ActivityStreams(activity_id=activity.id, samples=parsed.streams)
                db.add(activity.streams)
            else:
                activity.streams.samples = parsed.streams
        merged = parsed_from_activity(activity, parsed.source_metadata)
        if not merged.streams and parsed.streams:
            merged.streams = parsed.streams
        write_metrics(db, athlete_id, activity, merged, _reusable_stream_details(parsed, merged, stream_details))
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
                existing.streams = ActivityStreams(activity_id=existing.id, samples=parsed.streams)
                db.add(existing.streams)
            else:
                existing.streams.samples = parsed.streams
        merged = parsed_from_activity(existing, parsed.source_metadata)
        if not merged.streams and parsed.streams:
            merged.streams = parsed.streams
        write_metrics(db, athlete_id, existing, merged, _reusable_stream_details(parsed, merged, stream_details))
        db.commit()
        return existing, True

    activity = Activity(
        athlete_id=athlete_id,
        sport=parsed.sport,
        subtype=parsed.subtype,
        name=parsed.name,
        start_time=to_utc(parsed.start_time),
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
        rpe=parsed.rpe,
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
    write_metrics(db, athlete_id, activity, parsed, stream_details)
    db.commit()
    db.refresh(activity)
    return activity, False


def ingest_file(db: Session, athlete_id: int, path: str, source_type: str = "upload", import_session_id: int | None = None, original_name: str | None = None):
    display_name = original_name or Path(path).name
    fmt = activity_format(display_name) or activity_format(path)
    if not fmt:
        raise ValueError(f"Unsupported activity format: {display_name}")
    inner_suffix, compressed = fmt
    digest = sha256_file(path)
    os.makedirs(settings.storage_path, exist_ok=True)
    raw_suffix = Path(display_name).name.lower()
    matched_suffix = next((x for x in (".fit.gz", ".gpx.gz", ".tcx.gz", ".fit", ".gpx", ".tcx") if raw_suffix.endswith(x)), inner_suffix)
    athlete_storage = os.path.join(settings.storage_path, str(athlete_id))
    os.makedirs(athlete_storage, exist_ok=True)
    storage_key = os.path.join(athlete_storage, digest + matched_suffix)
    if not os.path.exists(storage_key):
        shutil.copy2(path, storage_key)
    raw = db.scalar(select(RawActivityFile).where(RawActivityFile.athlete_id == athlete_id, RawActivityFile.storage_key == storage_key))
    if raw is None:
        raw = RawActivityFile(
            athlete_id=athlete_id,
            import_session_id=import_session_id,
            filename=display_name,
            storage_key=storage_key,
            sha256=digest,
            parser_version=settings.metric_version,
        )
        db.add(raw)
        db.flush()

    parse_path = path
    temp_path = None
    try:
        if compressed:
            fd, temp_path = tempfile.mkstemp(suffix=inner_suffix)
            os.close(fd)
            _decompress_gzip_limited(path, temp_path)
            parse_path = temp_path
        parsed = PARSERS[inner_suffix](parse_path)
        parsed.source_metadata = {**(parsed.source_metadata or {}), "original_filename": display_name, "compressed": compressed}
        return ingest_parsed(db, athlete_id, parsed, source_type, raw_file_id=raw.id, import_session_id=import_session_id)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def ingest_zip(db: Session, athlete_id: int, zip_path: str, import_session: ImportSession, finalize: bool = True):
    members = list(safe_members(zip_path))
    # The parent upload itself counted as one staged file. Replace that count with
    # the number of actual supported activity files discovered inside the archive.
    import_session.discovered_count += max(0, len(members) - 1)
    import_session.status = "processing"
    db.commit()
    errors = []
    with zipfile.ZipFile(zip_path) as zf, tempfile.TemporaryDirectory() as tmp:
        for index, info in enumerate(members):
            try:
                original = safe_basename(info.filename)
                fmt = activity_format(original)
                if not fmt:
                    continue
                compound_suffix = next((x for x in (".fit.gz", ".gpx.gz", ".tcx.gz", ".fit", ".gpx", ".tcx") if original.lower().endswith(x)), Path(original).suffix.lower())
                target = os.path.join(tmp, f"{index}{compound_suffix}")
                extract_member(zf, info, target)
                _, duplicate = ingest_file(db, athlete_id, target, "zip", import_session.id, original_name=original)
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
