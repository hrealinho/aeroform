# Changelog

## v0.5.2 - Correctness pass on load, thresholds and the calendar

### Fixed - blocking

- Planned-workout create/update/delete/match returned 500 for every request: the audit
  trail wrote a raw `datetime` into a JSON column. The whole manual calendar was unusable.
- Stream-derived elevation gain/loss reported zero on ~1 Hz streams. The noise floor gated
  individual point-to-point deltas, and a 1000 m/h climb only moves 0.28 m per second, so
  every sample was discarded. The floor now gates direction reversals (peak-to-valley
  hysteresis), making the result independent of sampling rate. Affected GPX, TCX and
  Strava-stream imports, and therefore every terrain and composite load derived from them.
- `apply_to_history` on threshold estimation never committed its recomputation, so the
  backfill silently did nothing. It now runs as a Celery task that commits per activity.
- Manual thresholds defaulted to `valid_from = today`, so an entered FTP or resting HR
  could never apply to a stored activity. They now back-date to the first activity by
  default, and threshold changes trigger a recomputation.
- Threshold estimation never produced `resting_hr`, which `hr_trimp_load` requires, so
  HR-only activities were stuck on the session-RPE fallback. Resting HR is now inferred
  from the lowest sustained in-activity heart rate at low confidence.

### Fixed - correctness

- Activity fingerprints are normalized to UTC before bucketing. The same instant arriving
  as `+02:00` from a GPX file, as `Z` from Strava, or naive after a SQLite round-trip
  produced three different fingerprints and stored the activity three times.
- Timestamps are timezone-aware end to end: `utcnow()` model defaults, UTC-aware query
  bounds, and UTC normalization in the matcher. Removes the `TypeError` workaround that
  mutated ORM attributes during auto-matching.
- The AI command validator derives its simulation window from the commands, so workouts
  more than 42 days out can be edited. "Outside the validated planning window" is now
  distinct from "not found for this athlete".
- Deleting an objective or training block detaches dependent planned workouts and blocks
  first. Previously this violated a foreign key on Postgres and orphaned the reference on
  SQLite. `ondelete="SET NULL"` added, and SQLite foreign keys are now enforced.
- `/analytics/fitness` warms the EWMA over the preceding year, so a narrowed window
  reports real fitness instead of a curve ramping from zero.
- `FITNESS_TAU_DAYS` / `FATIGUE_TAU_DAYS` are read by the fitness and projection models.
  They were previously dead configuration.
- `load_kind` is validated on the query parameter. An unknown value in an empty date range
  used to return 200 with a plausible-looking series.
- Re-entering a manual threshold replaces the previous row instead of appending another
  open-ended one. Added `DELETE /api/v1/thresholds/{id}`.
- Zone totals, best-effort windows, normalized power, grade distribution and altitude
  smoothing are all derived from the stream's own timestamps rather than assuming one
  sample is one second.
- Sport classification uses moving time, derived from the stream when the source omits it,
  so a run with a long stop is no longer classified as a hike.
- Threshold confidence is keyed on effort evidence (heart rate relative to observed max),
  not just window length. A steady endurance ride no longer yields a high-confidence FTP.
- Hiking and mountaineering durability coefficients now exceed trail running's, matching
  the documented intent that these sports are driven by time on feet.
- Malformed Strava webhooks return 200 instead of raising, so Strava stops retrying.
- Frontend: create and drag-to-move send the same absolute-UTC instant, and all calendar
  dates use the local calendar day rather than the UTC one.
- Frontend: server-rendered pages call the API over `API_URL_INTERNAL`, and a failed
  dashboard fetch is shown instead of silently rendering zeros.

### Robustness

- Upload staging directories are cleaned up on rejection, on failure and after import.
- Eager (`ASYNC_TASKS=false`) task execution is offloaded so a large archive cannot block
  the event loop.
- Stream metrics are computed once per ingest instead of twice on the deduplication paths.
- The Strava resume cursor is stored separately from the error log and survives
  completion; `discovered_count` no longer double-counts pages on a resumed backfill.
- ZIP member size limits are enforced while extracting, not just against the declared
  header size. Archive caps lowered to 4 GB total / 200 MB per member.
- The demo-athlete helper tolerates a concurrent first request.
- CORS origins are configurable via `CORS_ORIGINS`.

### Tests and tooling

- Added HTTP-level coverage for every write endpoint - the gap that let the calendar
  500 ship with a green suite. 34 tests to 80.
- Added regressions for sampling-rate-independent elevation, timezone-invariant
  fingerprints, EWMA warm-up, the threshold pipeline and the command validation window.
- Added frontend tests for the local-calendar date helpers, plus `npm test` / `npm run typecheck`.
- Committed `package-lock.json` and switched the frontend image to `npm ci`.
- Single source of truth for the app version; `/health` and the OpenAPI schema agree.


## v0.5.1 - Import/load repair

- Fixed `ParsedActivity.rpe` missing from the parser DTO, which caused fallback load calculation to fail for athletes without configured thresholds.
- Propagated RPE through canonical activities and mapped Strava `perceived_exertion` when present.
- Fixed historical Strava backfills getting stuck in `PendingRollbackError` after a failed/duplicate flush.
- Prevented launching a second historical Strava sync while one is already queued/processing/paused.
- Stopped displaying unknown Strava historical elevation loss as `0 m`; unknown descent is now shown as `-`.
- Added `--repair-zero` / `--force` metric recomputation options and copied maintenance scripts into the Docker API image.

## v0.5.0 - Sport-aware terrain load

- Replaced the v0.4 mountain heuristic with transparent distance, ascent, descent and durability load dimensions.
- Added sport-specific composite training-load blending so trail running/hiking elevation affects fitness without blindly double-counting uphill cardiovascular stress.
- Added explicit downhill/eccentric load with stronger descent weighting for trail/mountain sports.
- Added FIT sub-sport classification, conservative GPX sport inference, classification confidence/reason metadata, and manual classification override with metric recomputation.
- Added GPX HR/cadence extension parsing and smoothed elevation gain/loss estimation for GPX/TCX.
- Added Strava stream-derived elevation loss and grade/vertical stream metrics.
- Added overall/metabolic/mechanical/ascent/descent/durability analytics modes and richer weekly aggregates.
- Added dashboard load explorer and weekly load-profile visualization.
- Extended Athlete Context and Coach vertical analysis with descent and mechanical load.
- Added a metric recomputation script for existing v0.4 databases.
- Expanded the backend suite to 28 passing tests.

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
