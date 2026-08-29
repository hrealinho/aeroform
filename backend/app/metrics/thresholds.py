from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from statistics import median
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.domain.models import Activity, ActivityStreams, AthleteThreshold
from app.metrics.load import resolve_durations
from app.metrics.terrain import sample_durations


# These methods derive a value from a whole-activity average, which includes coasting,
# descents and junctions. They produce a LOWER BOUND, never a threshold, and must never be
# persisted as one: a too-low FTP inflates every subsequent load by (real/stored)^2, so
# storing a floor is worse than storing nothing and falling back to RPE.
LOWER_BOUND_METHODS = {
    "best_sustained_activity_average_power",
    "best_sustained_activity_average_speed",
    "best_sustained_activity_average_hr",
}


@dataclass
class ThresholdEstimate:
    sport: str
    metric: str
    value: float
    confidence: str
    method: str
    evidence: dict

    @property
    def is_lower_bound(self) -> bool:
        return self.method in LOWER_BOUND_METHODS

    def as_dict(self) -> dict:
        return {
            "sport": self.sport,
            "metric": self.metric,
            "value": round(self.value, 4),
            "confidence": self.confidence,
            "method": self.method,
            "evidence": self.evidence,
            "is_lower_bound": self.is_lower_bound,
        }


def _finite(value) -> float | None:
    if isinstance(value, (int, float)) and isfinite(float(value)):
        return float(value)
    return None


def _series(samples: list[dict], key: str) -> list[float | None]:
    return [_finite(s.get(key)) for s in samples]


def _rolling_extreme_average(samples: list[dict], key: str, seconds: int, want_max: bool, min_coverage: float = 0.85,
                             durations: list[float] | None = None) -> float | None:
    """Best (or lowest) duration-weighted rolling mean over a window of real seconds.

    The window is measured with the stream's own timestamps rather than by counting
    samples, so a 30-minute best effort means 30 minutes whether the source recorded
    at 1 Hz or every 5 seconds. A window must be covered by enough real samples to be
    trustworthy; gaps and dropouts are tolerated up to ``min_coverage``.
    """
    if seconds <= 0 or not samples:
        return None
    values = _series(samples, key)
    # Callers sweeping many windows over one stream pass durations in: deriving them
    # re-parses every timestamp, which dominated the cost of building a power curve.
    if durations is None:
        durations = sample_durations(samples)
    if sum(durations) < seconds:
        return None

    required = seconds * min_coverage
    weighted = 0.0   # sum(value * duration) over present samples in the window
    covered = 0.0    # seconds of present samples in the window
    span = 0.0       # total seconds in the window, present or not
    best = None
    left = 0
    for right, value in enumerate(values):
        span += durations[right]
        if value is not None:
            weighted += value * durations[right]
            covered += durations[right]
        # Shrink from the left until the window is no longer than the target span.
        while left <= right and span - durations[left] >= seconds:
            if values[left] is not None:
                weighted -= values[left] * durations[left]
                covered -= durations[left]
            span -= durations[left]
            left += 1
        if span >= seconds and covered >= required:
            avg = weighted / covered
            if best is None or (avg > best if want_max else avg < best):
                best = avg
    return best


def rolling_best_average(samples: list[dict], key: str, seconds: int, min_coverage: float = 0.85,
                         durations: list[float] | None = None) -> float | None:
    """Highest duration-weighted rolling mean over ``seconds`` of real time."""
    return _rolling_extreme_average(samples, key, seconds, want_max=True, min_coverage=min_coverage, durations=durations)


def rolling_lowest_average(samples: list[dict], key: str, seconds: int, min_coverage: float = 0.85,
                           durations: list[float] | None = None) -> float | None:
    """Lowest duration-weighted rolling mean over ``seconds`` of real time."""
    return _rolling_extreme_average(samples, key, seconds, want_max=False, min_coverage=min_coverage, durations=durations)


def _max_effort(activities: list[Activity], key: str, seconds: int) -> tuple[float | None, int | None]:
    best, best_id = None, None
    for activity in activities:
        samples = activity.streams.samples if activity.streams and activity.streams.samples else []
        value = rolling_best_average(samples, key, seconds)
        if value is not None and (best is None or value > best):
            best, best_id = value, activity.id
    return best, best_id


def _working_seconds(activity: Activity) -> float:
    """Seconds the athlete was actually working.

    Activity averages describe moving time, so selecting candidate efforts by elapsed time
    let a short ride with a three-hour cafe stop qualify as a sustained effort.
    """
    return resolve_durations(activity.duration_s, activity.moving_time_s).metabolic_s


def _best_activity_average(activities: list[Activity], key: str, min_duration_s: int, max_duration_s: int | None = None) -> tuple[float | None, int | None]:
    best, best_id = None, None
    attr = {"power": "avg_power", "hr": "avg_hr"}.get(key)
    for a in activities:
        working = _working_seconds(a)
        if working < min_duration_s or (max_duration_s and working > max_duration_s):
            continue
        value = _finite(getattr(a, attr, None)) if attr else None
        if value is not None and (best is None or value > best):
            best, best_id = value, a.id
    return best, best_id


# A "best 60-minute mean power" only equals threshold if the athlete actually went
# near-maximal. Without that check an athlete who only rides steady endurance gets an
# FTP equal to their endurance power, which then inflates the intensity factor of every
# subsequent ride. Effort intent is inferred from how hard the source activity's heart
# rate was relative to the observed maximum, which is the only evidence available from
# history alone.
MAXIMAL_HR_FRACTION = 0.88
HARD_HR_FRACTION = 0.80


def _effort_evidence(activities: list[Activity], activity_id: int | None, observed_max_hr: float | None) -> dict:
    """Describe how maximal the source effort looked, for confidence weighting."""
    activity = next((a for a in activities if a.id == activity_id), None)
    if activity is None:
        return {"effort": "unknown", "reason": "source activity unavailable"}
    avg_hr = _finite(activity.avg_hr)
    if not avg_hr or not observed_max_hr:
        return {"effort": "unknown", "reason": "no heart-rate evidence for the source effort"}
    fraction = avg_hr / observed_max_hr
    if fraction >= MAXIMAL_HR_FRACTION:
        effort = "maximal"
    elif fraction >= HARD_HR_FRACTION:
        effort = "hard"
    else:
        effort = "submaximal"
    return {
        "effort": effort,
        "avg_hr": round(avg_hr, 1),
        "observed_max_hr": round(observed_max_hr, 1),
        "hr_fraction_of_max": round(fraction, 3),
        "reason": f"source effort averaged {fraction:.0%} of observed max HR",
    }


def _confidence_from_effort(has_long_window: bool, corroborating: int, effort: dict) -> str:
    """Confidence keys off effort evidence first, window length second.

    A submaximal source caps confidence at low no matter how long the window was: the
    estimate is then a floor on threshold, not a measurement of it.
    """
    kind = effort.get("effort")
    if kind == "submaximal":
        return "low"
    if kind == "maximal" and has_long_window and corroborating >= 2:
        return "high"
    if kind in {"maximal", "hard"} and has_long_window:
        return "medium"
    if kind == "unknown":
        return "low" if not has_long_window else "medium"
    return "medium" if corroborating >= 2 else "low"


def _estimate_power_threshold(activities: list[Activity], sport: str, observed_max_hr: float | None = None) -> ThresholdEstimate | None:
    if not activities:
        return None
    if sport == "cycling":
        windows = [(3600, 1.00, "60m"), (2400, 0.98, "40m"), (1200, 0.95, "20m")]
        metric = "ftp"
    else:
        windows = [(2400, 1.00, "40m"), (1800, 0.98, "30m"), (1200, 0.95, "20m")]
        metric = "critical_power"

    candidates: list[tuple[float, str, int | None]] = []
    evidence = []
    for seconds, factor, label in windows:
        value, activity_id = _max_effort(activities, "power", seconds)
        if value:
            estimate = value * factor
            candidates.append((estimate, label, activity_id))
            evidence.append({"window": label, "best_mean_power_w": round(value, 1), "factor": factor, "estimate_w": round(estimate, 1), "activity_id": activity_id})

    if not candidates:
        # Low-confidence fallback from activity averages. This is intentionally not based on NP,
        # which can overstate sustainable threshold on highly variable sessions.
        value, activity_id = _best_activity_average(activities, "power", 1200, 5400)
        if not value:
            return None
        return ThresholdEstimate(sport, metric, value * 0.95, "low", "best_sustained_activity_average_power", {
            "activity_id": activity_id, "average_power_w": round(value, 1), "factor": 0.95,
            "effort": _effort_evidence(activities, activity_id, observed_max_hr),
            "caveat": "Derived from an activity average without a best-effort window; treat as indicative only.",
        })

    # Use the strongest supported sustained estimate, then let the effort evidence for
    # that specific activity decide how much to trust it.
    value, best_label, best_activity_id = max(candidates, key=lambda c: c[0])
    has_long = best_label in {"40m", "60m"}
    effort = _effort_evidence(activities, best_activity_id, observed_max_hr)
    confidence = _confidence_from_effort(has_long, len(evidence), effort)
    caveat = None
    if effort.get("effort") == "submaximal":
        caveat = "Source effort does not look maximal, so this is a lower bound on threshold rather than a measurement."
    return ThresholdEstimate(sport, metric, value, confidence, "maximal_mean_power_history", {
        "candidates": evidence, "window_used": best_label, "effort": effort, "caveat": caveat,
    })


def _estimate_threshold_hr(activities: list[Activity], sport: str, observed_max_hr: float | None) -> ThresholdEstimate | None:
    # A 30-minute hard steady effort is the preferred history-derived proxy. If unavailable,
    # a 20-minute rolling value or hard-session average is used with lower confidence.
    h30, id30 = _max_effort(activities, "hr", 1800)
    h20, id20 = _max_effort(activities, "hr", 1200)
    value = h30 or h20
    activity_id = id30 if h30 else id20
    method = "best_30m_rolling_hr" if h30 else "best_20m_rolling_hr"
    confidence = "medium" if h30 else "low"
    if value is None:
        value, activity_id = _best_activity_average(activities, "hr", 1800, 7200)
        method = "best_sustained_activity_average_hr"
        confidence = "low"
    if value is None:
        return None
    if observed_max_hr:
        # Prevent a short anomalous sensor spike / unusually hard VO2 block from producing an
        # implausibly high LTHR. This is a guardrail, not a physiological identity.
        value = min(value, observed_max_hr * 0.97)
    return ThresholdEstimate(sport, "threshold_hr", value, confidence, method, {"activity_id": activity_id, "observed_max_hr": observed_max_hr})


def _estimate_threshold_speed(activities: list[Activity], observed_max_hr: float | None = None) -> ThresholdEstimate | None:
    # Only road/general running is used. Trail grade/terrain makes raw speed unsuitable as a
    # threshold-pace estimator. Best 60m is direct evidence; shorter windows are discounted.
    windows = [(3600, 1.00, "60m"), (2700, 0.98, "45m"), (1800, 0.95, "30m")]
    candidates: list[tuple[float, str, int | None]] = []
    evidence = []
    for seconds, factor, label in windows:
        value, activity_id = _max_effort(activities, "speed", seconds)
        if value:
            estimate = value * factor
            candidates.append((estimate, label, activity_id))
            evidence.append({"window": label, "best_mean_speed_mps": round(value, 3), "factor": factor, "estimate_mps": round(estimate, 3), "activity_id": activity_id})
    if not candidates:
        best, best_id = None, None
        for a in activities:
            working = _working_seconds(a)
            if not a.distance_m or working < 1800 or working > 7200:
                continue
            speed = a.distance_m / working
            if best is None or speed > best:
                best, best_id = speed, a.id
        if best is None:
            return None
        return ThresholdEstimate("running", "threshold_speed_mps", best * 0.95, "low", "best_sustained_activity_average_speed", {
            "activity_id": best_id, "factor": 0.95,
            "effort": _effort_evidence(activities, best_id, observed_max_hr),
            "caveat": "Derived from an activity average without a best-effort window; treat as indicative only.",
        })
    value, best_label, best_activity_id = max(candidates, key=lambda c: c[0])
    effort = _effort_evidence(activities, best_activity_id, observed_max_hr)
    confidence = _confidence_from_effort(best_label == "60m", len(evidence), effort)
    return ThresholdEstimate("running", "threshold_speed_mps", value, confidence, "maximal_mean_speed_history", {
        "candidates": evidence, "window_used": best_label, "effort": effort,
        "caveat": "Source effort does not look maximal, so this is a lower bound." if effort.get("effort") == "submaximal" else None,
    })


# hr_trimp_load needs BOTH resting and max HR. Estimating only max HR left every
# HR-only activity falling through to the session-RPE fallback, so resting HR is
# inferred too. The lowest sustained in-activity HR is a proxy for true resting HR
# (it is normally recorded standing still before the warm-up), so it is deliberately
# published at low confidence and clamped to a physiologically plausible band.
RESTING_HR_MIN = 30.0
RESTING_HR_MAX = 90.0
RESTING_HR_WINDOW_S = 60


def _estimate_resting_hr(activities: list[Activity]) -> ThresholdEstimate | None:
    per_activity: list[float] = []
    for activity in activities:
        samples = activity.streams.samples if activity.streams and activity.streams.samples else []
        if not samples:
            continue
        value = rolling_lowest_average(samples, "hr", RESTING_HR_WINDOW_S)
        if value is not None and RESTING_HR_MIN <= value <= RESTING_HR_MAX + 40:
            per_activity.append(value)
    if not per_activity:
        return None
    # Median across activities, so one session with a misreading strap cannot set it.
    value = min(max(median(per_activity), RESTING_HR_MIN), RESTING_HR_MAX)
    return ThresholdEstimate("global", "resting_hr", value, "low", "lowest_sustained_in_activity_hr", {
        "activities_sampled": len(per_activity),
        "window_s": RESTING_HR_WINDOW_S,
        "lowest_observed": round(min(per_activity), 1),
        "caveat": "Proxy for resting HR taken from in-activity minima, not a morning measurement. Enter a manual value to override.",
    })


def estimate_thresholds(db: Session, athlete_id: int, history_days: int = 365) -> list[ThresholdEstimate]:
    since = utcnow() - timedelta(days=history_days)
    activities = list(db.scalars(select(Activity).where(Activity.athlete_id == athlete_id, Activity.start_time >= since).order_by(Activity.start_time)))
    observed_hr = [float(a.max_hr) for a in activities if a.max_hr and 100 <= a.max_hr <= 230]
    observed_max_hr = max(observed_hr) if observed_hr else None
    estimates: list[ThresholdEstimate] = []
    if observed_max_hr:
        estimates.append(ThresholdEstimate("global", "max_hr", observed_max_hr, "medium", "highest_observed_activity_hr", {"history_days": history_days, "samples": len(observed_hr)}))

    resting = _estimate_resting_hr(activities)
    if resting:
        estimates.append(resting)

    cycling = [a for a in activities if a.sport == "cycling"]
    running = [a for a in activities if a.sport == "running"]
    trail = [a for a in activities if a.sport == "trail_running"]

    for sport, items in (("cycling", cycling), ("running", running), ("trail_running", trail)):
        if sport in {"cycling", "running"}:
            p = _estimate_power_threshold(items, sport, observed_max_hr)
            if p:
                estimates.append(p)
        hr = _estimate_threshold_hr(items, sport, observed_max_hr)
        if hr:
            estimates.append(hr)

    pace = _estimate_threshold_speed(running, observed_max_hr)
    if pace:
        estimates.append(pace)
    return estimates


def first_activity_date(db: Session, athlete_id: int) -> date | None:
    """Earliest activity date, used to backdate thresholds so they cover real history."""
    earliest = db.scalar(select(Activity.start_time).where(Activity.athlete_id == athlete_id).order_by(Activity.start_time).limit(1))
    return earliest.date() if earliest else None



# hr_trimp_load needs resting AND max HR. Resting HR can only be inferred from an HR
# stream, and a summary-only Strava backfill has none, so on that kind of history it can
# never be produced automatically. Rather than leave the athlete guessing why every
# activity says "session_rpe", the API states what is missing and what it would unlock.
def missing_threshold_advice(db: Session, athlete_id: int) -> list[dict]:
    rows = {(r.sport, r.metric) for r in _current_rows(db, athlete_id)}
    metrics = {metric for _, metric in rows}
    advice: list[dict] = []

    hr_activities = db.scalar(select(func.count(Activity.id)).where(
        Activity.athlete_id == athlete_id, Activity.avg_hr.isnot(None),
    )) or 0
    power_activities = db.scalar(select(func.count(Activity.id)).where(
        Activity.athlete_id == athlete_id, Activity.avg_power.isnot(None),
    )) or 0
    streams = db.scalar(select(func.count(ActivityStreams.id)).join(
        Activity, Activity.id == ActivityStreams.activity_id,
    ).where(Activity.athlete_id == athlete_id)) or 0

    if hr_activities and "resting_hr" not in metrics:
        advice.append({
            "metric": "resting_hr",
            "severity": "high",
            "message": (
                f"{hr_activities} activities have average heart rate but no resting HR is set, so "
                "heart-rate load cannot be calculated and those activities fall back to a "
                "duration-based estimate."
            ),
            "action": "Enter your resting HR manually (sport: global, metric: resting_hr).",
            "auto_estimable": streams > 0,
        })
    if hr_activities and "max_hr" not in metrics:
        advice.append({
            "metric": "max_hr", "severity": "high",
            "message": "No max HR is set, which heart-rate load also requires.",
            "action": "Estimate from history, or enter a tested value.",
            "auto_estimable": True,
        })
    if power_activities and not ({"ftp", "critical_power"} & metrics):
        advice.append({
            "metric": "ftp", "severity": "medium",
            "message": f"{power_activities} activities have power but no threshold power is set.",
            "action": "Estimate from history, or enter a tested FTP.",
            "auto_estimable": True,
        })
    if streams == 0:
        advice.append({
            "metric": "streams", "severity": "info",
            "message": (
                "No activity streams are stored, so best-effort windows, power/HR zone time and "
                "decoupling cannot be calculated. Threshold estimates fall back to activity averages."
            ),
            "action": "Set STRAVA_SYNC_STREAMS=true before a backfill, or import original FIT/GPX files.",
            "auto_estimable": False,
        })
    return advice

def _current_rows(db: Session, athlete_id: int, at: date | None = None) -> list[AthleteThreshold]:
    at = at or date.today()
    rows = list(db.scalars(select(AthleteThreshold).where(
        AthleteThreshold.athlete_id == athlete_id,
        AthleteThreshold.valid_from <= at,
    )))
    valid = [r for r in rows if r.valid_until is None or r.valid_until >= at]
    # Manual wins ties. Latest valid_from wins otherwise.
    valid.sort(key=lambda r: (r.sport, r.metric, r.valid_from, 1 if r.source == "manual" else 0), reverse=True)
    result, seen = [], set()
    for row in valid:
        key = (row.sport, row.metric)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def persist_estimates(db: Session, athlete_id: int, estimates: list[ThresholdEstimate],
                      valid_from: date | None = None, apply_if_manual_missing: bool = True,
                      ) -> tuple[list[AthleteThreshold], list[ThresholdEstimate]]:
    """Store the estimates worth storing. Returns (saved rows, advisory estimates).

    Lower-bound estimates are deliberately NOT stored. Without streams, threshold power
    falls back to the best whole-activity average, which is a floor - and a floor stored as
    an FTP silently inflates the load of every ride that follows it. Reporting it as
    advisory keeps the information without corrupting the metrics.
    """
    valid_from = valid_from or date.today()
    current = {(r.sport, r.metric): r for r in _current_rows(db, athlete_id, valid_from)}
    saved: list[AthleteThreshold] = []
    advisory: list[ThresholdEstimate] = []
    for estimate in estimates:
        key = (estimate.sport, estimate.metric)
        existing = current.get(key)
        if apply_if_manual_missing and existing and existing.source == "manual":
            continue
        if estimate.is_lower_bound:
            advisory.append(estimate)
            continue
        # Close any previous inferred active estimate. Manual history is never altered.
        old_auto = list(db.scalars(select(AthleteThreshold).where(
            AthleteThreshold.athlete_id == athlete_id,
            AthleteThreshold.sport == estimate.sport,
            AthleteThreshold.metric == estimate.metric,
            AthleteThreshold.source.like("estimated%"),
            AthleteThreshold.valid_until.is_(None),
        )))
        for old in old_auto:
            if old.valid_from < valid_from:
                old.valid_until = valid_from - timedelta(days=1)
            elif old.valid_from == valid_from:
                db.delete(old)
        row = AthleteThreshold(
            athlete_id=athlete_id, sport=estimate.sport, metric=estimate.metric,
            value=estimate.value, valid_from=valid_from, source="estimated_history",
            confidence=estimate.confidence,
        )
        db.add(row)
        saved.append(row)
    db.commit()
    for row in saved:
        db.refresh(row)
    return saved, advisory


def _range(label: str, low: float | None, high: float | None, unit: str) -> dict:
    return {"label": label, "min": round(low, 2) if low is not None else None, "max": round(high, 2) if high is not None else None, "unit": unit}


def power_zones(threshold_power: float, sport: str = "cycling") -> list[dict]:
    if sport == "cycling":
        # Coggan-style 7-zone boundaries as percentages of FTP.
        bounds = [("Z1 Recovery", None, .55), ("Z2 Endurance", .55, .75), ("Z3 Tempo", .75, .90), ("Z4 Threshold", .90, 1.05), ("Z5 VO2", 1.05, 1.20), ("Z6 Anaerobic", 1.20, 1.50), ("Z7 Neuromuscular", 1.50, None)]
    else:
        # Simpler critical-power running zones. Running-power ecosystems differ, so the method is
        # named and versioned instead of presented as a universal standard.
        bounds = [("Z1 Recovery", None, .80), ("Z2 Endurance", .80, .90), ("Z3 Steady", .90, .95), ("Z4 Threshold", .95, 1.05), ("Z5 High intensity", 1.05, 1.15), ("Z6 Anaerobic", 1.15, None)]
    return [_range(label, threshold_power * lo if lo else None, threshold_power * hi if hi else None, "W") for label, lo, hi in bounds]


def hr_zones(threshold_hr: float) -> list[dict]:
    # LTHR-relative zones (Friel-style family of boundaries). These are training zones, not a
    # medical classification.
    bounds = [("Z1 Recovery", None, .85), ("Z2 Aerobic", .85, .90), ("Z3 Tempo", .90, .95), ("Z4 Threshold", .95, 1.00), ("Z5a Super-threshold", 1.00, 1.03), ("Z5b Aerobic capacity", 1.03, 1.06), ("Z5c Anaerobic", 1.06, None)]
    return [_range(label, threshold_hr * lo if lo else None, threshold_hr * hi if hi else None, "bpm") for label, lo, hi in bounds]


def pace_zones(threshold_speed_mps: float) -> list[dict]:
    # Pace is inverse to speed; API returns speed bounds plus user-friendly pace strings.
    bounds = [("Z1 Recovery", None, .80), ("Z2 Endurance", .80, .90), ("Z3 Steady", .90, .95), ("Z4 Threshold", .95, 1.02), ("Z5 Interval", 1.02, 1.10), ("Z6 Repetition", 1.10, None)]
    zones = []
    for label, lo, hi in bounds:
        min_speed = threshold_speed_mps * lo if lo else None
        max_speed = threshold_speed_mps * hi if hi else None
        def pace(speed):
            if not speed:
                return None
            seconds = 1000.0 / speed
            return f"{int(seconds // 60)}:{int(round(seconds % 60)):02d}/km"
        zones.append({"label": label, "min_speed_mps": round(min_speed, 3) if min_speed else None, "max_speed_mps": round(max_speed, 3) if max_speed else None, "faster_than": pace(max_speed), "slower_than": pace(min_speed), "unit": "pace"})
    return zones


def current_thresholds_and_zones(db: Session, athlete_id: int) -> dict:
    rows = _current_rows(db, athlete_id)
    out = []
    for row in sorted(rows, key=lambda r: (r.sport, r.metric)):
        item = {"id": row.id, "sport": row.sport, "metric": row.metric, "value": row.value, "valid_from": row.valid_from.isoformat(), "source": row.source, "confidence": row.confidence}
        if row.source != "manual" and row.confidence == "low":
            item["caveat"] = "Estimated at low confidence, most likely because no near-maximal effort was found in the history. Treat it as a lower bound and override it with a tested value."
        if row.metric == "resting_hr" and row.source != "manual":
            item["caveat"] = "Inferred from the lowest sustained in-activity heart rate, not a morning measurement. Enter a manual value if you have one."
        if row.metric in {"ftp", "critical_power"}:
            item["zones"] = power_zones(row.value, row.sport)
        elif row.metric == "threshold_hr":
            item["zones"] = hr_zones(row.value)
        elif row.metric == "threshold_speed_mps":
            item["zones"] = pace_zones(row.value)
            seconds = 1000.0 / row.value
            item["display_value"] = f"{int(seconds // 60)}:{int(round(seconds % 60)):02d}/km"
        out.append(item)
    return {"thresholds": out, "advice": missing_threshold_advice(db, athlete_id)}


def upsert_manual_threshold(db: Session, athlete_id: int, sport: str, metric: str, value: float, valid_from: date) -> AthleteThreshold:
    """Record a manual threshold without accumulating duplicate open-ended rows.

    Re-entering the same sport/metric used to append another row with no end date, so a
    typo could never be corrected and tie-breaking between identical rows was arbitrary.
    A repeat entry for the same effective date now replaces the previous one, and an
    entry with a later effective date closes the one it supersedes.
    """
    existing = list(db.scalars(select(AthleteThreshold).where(
        AthleteThreshold.athlete_id == athlete_id,
        AthleteThreshold.sport == sport,
        AthleteThreshold.metric == metric,
        AthleteThreshold.source == "manual",
    )))
    for row in existing:
        if row.valid_from == valid_from:
            row.value = value
            row.valid_until = None
            row.confidence = "high"
            db.commit()
            db.refresh(row)
            return row
    for row in existing:
        if row.valid_from < valid_from and (row.valid_until is None or row.valid_until >= valid_from):
            row.valid_until = valid_from - timedelta(days=1)
    row = AthleteThreshold(
        athlete_id=athlete_id, sport=sport, metric=metric, value=value,
        valid_from=valid_from, source="manual", confidence="high",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_threshold(db: Session, athlete_id: int, threshold_id: int) -> bool:
    """Remove one threshold row and reopen the manual row it superseded, if any."""
    row = db.get(AthleteThreshold, threshold_id)
    if not row or row.athlete_id != athlete_id:
        return False
    sport, metric, source, valid_from = row.sport, row.metric, row.source, row.valid_from
    db.delete(row)
    db.flush()
    if source == "manual":
        previous = [r for r in db.scalars(select(AthleteThreshold).where(
            AthleteThreshold.athlete_id == athlete_id,
            AthleteThreshold.sport == sport,
            AthleteThreshold.metric == metric,
            AthleteThreshold.source == "manual",
        )) if r.valid_from < valid_from]
        if previous:
            latest = max(previous, key=lambda r: r.valid_from)
            if latest.valid_until == valid_from - timedelta(days=1):
                latest.valid_until = None
    db.commit()
    return True
