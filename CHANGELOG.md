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
