"""Recompute canonical activity metrics using the currently configured metric version.

Useful when upgrading from v0.4 to v0.5: no raw files need to be re-imported because
canonical activities and stored streams are sufficient for the new terrain-aware load.
"""
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.domain.models import Activity, ActivityMetrics
from app.services.ingestion import recalculate_activity_metrics


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(select(Activity, ActivityMetrics).outerjoin(ActivityMetrics).order_by(Activity.id)).all()
        updated = skipped = failed = 0
        for activity, metrics in rows:
            if metrics is not None and metrics.metric_version == settings.metric_version:
                skipped += 1
                continue
            try:
                recalculate_activity_metrics(db, activity.athlete_id, activity, {
                    "classification": {
                        "sport": activity.sport,
                        "confidence": (metrics.details or {}).get("classification", {}).get("confidence", "existing") if metrics else "existing",
                        "reason": "metric-version recomputation",
                        "source": "recompute",
                    }
                })
                db.commit()
                updated += 1
            except Exception as exc:
                db.rollback()
                failed += 1
                print(f"activity {activity.id}: {exc}")
        print(f"metric version: {settings.metric_version}; updated={updated}; skipped={skipped}; failed={failed}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
