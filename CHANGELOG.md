# Changelog

## v0.3.0 - Planning vertical slice

- Added deterministic planned-workout load estimation with versioned intensity factors.
- Added full planned-workout CRUD, lock protection, move/edit audit history, and manual matching.
- Added automatic planned-vs-actual matching using sport, time, duration, and distance confidence scoring.
- Added future fitness/fatigue/form projection from planned load with actual/projected separation.
- Added explainable planning warnings for aggressive weekly load ramps and stacked key sessions.
- Added objectives and training-block CRUD APIs and planning-constraint persistence.
- Replaced the calendar scaffold with a working weekly planner, HTML5 drag/drop, planned-vs-actual display, workout creation, locking, deletion, auto-match, and load summaries.
- Replaced the season scaffold with objectives, training-block creation, a season timeline, and projected fitness graph.
- Added planning unit tests.

## v0.2.0 - Strava and async ingestion

- Added Strava OAuth, encrypted token storage, history synchronization, webhook handling, and disconnect flow.
- Added Celery/Redis asynchronous file and Strava import jobs.
- Added richer stream metrics including normalized power, zones, aerobic decoupling, and weekly analytics.

## v0.1.0 - Data foundation

- Initial canonical activity model, FIT/GPX/TCX/ZIP import, training load, fitness/fatigue/form, and basic frontend.

## v0.4.0 - Grounded AI coach and Strava archive hardening

- Added Athlete Context aggregation over derived training metrics, thresholds, objectives, blocks, constraints, preferences and plan state.
- Added persistent Ask Coach endpoint and UI with evidence-backed deterministic analysis plus optional remote-provider synthesis.
- Added weekly plan generation and current-week adaptation.
- Added AIProposal persistence, structured plan commands, deterministic validation, approve/reject flow and re-validation at apply time.
- Added AI-initiated plan audit records and workout locking enforcement.
- Added "Why this session?" explanations with objective/block and recent-comparable-session context.
- Added coach preferences for weekly availability, long day, rest day and doubles.
- Added local and vendor-neutral HTTP JSON AI provider adapters.
- Added `.fit.gz`, `.gpx.gz` and `.tcx.gz` ingestion for Strava bulk exports.
- Added macOS `__MACOSX` / `._*` filtering so resource forks are not misread as activities.
- Scoped raw file storage by athlete for safer future SaaS multi-tenancy.
- Added archive inspection utility and expanded the backend suite to 20 tests.
