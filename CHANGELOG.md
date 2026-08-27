# Changelog

## v0.2.0

- Added Strava OAuth, encrypted token persistence and refresh.
- Added paginated, rate-limit-aware historical Strava backfill.
- Added Strava webhook processing for activity changes and deauthorization.
- Added explicit Strava disconnect/revoke flow.
- Added Celery/Redis worker architecture and Docker worker service.
- Moved file/ZIP processing behind import sessions and background task dispatch.
- Added live import progress UI.
- Added stream-derived normalized power, HR/power zone time and aerobic decoupling.
- Added weekly load/volume/elevation/mechanical-load analytics.
- Added richer activity metrics visibility.

## v0.1.0

- Initial modular monolith.
- Canonical activity model.
- FIT/GPX/TCX/ZIP parsing.
- Basic load, fitness/fatigue/form and objective/planning scaffolding.
