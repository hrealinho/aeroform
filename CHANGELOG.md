# Changelog

## v0.8.1 - Resetting plans and deleting target races

### Added

- **`DELETE /api/v1/plan`** clears training blocks and planned workouts. Defaults to
  future-only, because a reset should not erase the record of what was prescribed for training
  already done, and keeps workouts matched to completed activities for the same reason.
  `scope=all` and `include_locked=true` widen it. Everything removed is audited.
- **`DELETE /api/v1/objectives/{id}?with_plan=true`** deletes a target race together with its
  blocks and workouts. Without the flag the plan is detached and survives, which is what you
  want when swapping one race for another on the same build.
- **`force=true` on `DELETE /api/v1/planned-workouts/{id}`** to remove a locked workout.
- Season page: *Clear future plan* / *Clear everything*, both behind a confirm step, and
  per-race *Delete* offering "race only" or "race + plan".

### Fixed

- **A race workout could never be deleted.** The season planner creates it `locked=True` and
  `delete_planned` refused locked workouts unconditionally, so the race was permanently stuck
  in the calendar - and deleting its objective merely orphaned it.
- `DELETE /objectives/{id}` now returns what it removed rather than a bare 204.


## v0.8.0 - Executable prescriptions

Sessions are now prescribed the way a coach writes them, in the unit the athlete thinks in,
with a target they can actually execute.

### Added

- **Time or distance, athlete's choice.** `prescription: auto | time | distance` on the coach
  profile. Auto gives runners distance and everyone else time. Both are always populated -
  they are the same session described two ways - with `unit` recording which was authoritative
  so rounding never drifts the intended one.
- **Pace and power targets** (`app/planning/prescription.py`). Every step carries a band:
  `3:55-4:10/km` for running off `threshold_speed_mps`, `238-262 W` for cycling off FTP.
  Ranges rather than single numbers, because an exact number invites chasing it on a day when
  that is not appropriate. No threshold means no target, rather than an invented one.
- **A session library** (`app/planning/sessions.py`) of 15 named sessions declared as data:
  1km repeats, Tempo 3-2-1, Fast 8-4-2s, Rolling 400s, Broken miles, On-off kilometres, Race
  pace fartlek, Uphill threshold, progressive and race-practice long runs, and more. Replaces
  two hardcoded shapes that left every other intensity as one undifferentiated block - so a
  long run, a recovery run and a marathon-pace tempo were structurally identical.
- **The race is in the plan.** `plan-season` creates a locked planned workout on the objective
  date. Previously the taper simply stopped and the projection had no load on the day that
  matters most.
- **Explicit rest days**, so a blank day is distinguishable from a deliberate one. Zero
  duration, zero load, and `infer_intensity` reports `rest` rather than `recovery`.
- **`scripts/check_ai_provider.py`** exercises the configured provider against a real plan and
  audits the response against the rules the system prompt sets - session count, days used,
  volume drift, command shape. None of that is enforced by the schema, so a provider quietly
  ignoring the contract is now visible.

### Fixed

- Pace formatting rounded seconds after splitting, so 299.7s/km displayed as `4:60/km`.
- Interval volume scaled without limit: a nine-hour week produced `11x1km`. Capped - extra
  weekly volume belongs in easy running, not in more reps.
- A recovery run in a big week scaled to 61 minutes. Per-session ceilings added.
- Non-scalable ladders under-delivered their slot (a 60-minute session rendering as 49), so the
  week came in under target. Leftover time is absorbed by the cool-down.


## v0.7.2 - Taper fixes, and provider config that degrades instead of failing

### Fixed

- **A misconfigured `AI_PROVIDER` took down planning.** `get_provider()` was called outside
  the caller's try/except in all three coach endpoints, so a missing SDK or unresolvable API
  key raised during construction and returned a 500 rather than falling back to the
  deterministic planner. Replaced with `resolve_provider()`, which returns the local provider
  plus the reason, and the reason is surfaced in the response.
- **The taper did not taper.** Both taper weeks sat at the same 55% of peak. Now three weeks
  for a long objective (two for a short one) descending 80% / 55% / 30%, so volume comes off
  gradually while intensity is retained. Checked against a 19-week commercial marathon plan
  running roughly 81% / 56% / 19% across its taper.
- **The "peak" phase was not the peak.** It sat at 0.85 while "specific" sat at 1.00, so the
  highest week of the plan landed in the wrong phase and a reader could not tell where the
  plan topped out.


## v0.7.2 - OpenAI provider

- **`AI_PROVIDER=openai`** works alongside `anthropic`, `local` and `http_json`.
- The provider-agnostic parts - system prompt, context slice, output schema - moved to a shared
  `StructuredLLMProvider` base. A vendor now implements one method, `_parse`. Swapping vendors
  therefore changes no training logic, and a test asserts both receive byte-identical
  instructions and payloads.
- Vendor refusals differ in shape and both are handled: Claude reports `stop_reason: "refusal"`
  on the response, OpenAI populates `message.refusal`. Either raises, so the caller falls back
  to the validated deterministic seed.


## v0.7.1 - Claude as the plan refiner

- **`AI_PROVIDER=anthropic`** adds a real Claude provider (`anthropic` SDK 1.2.0, `claude-opus-5`,
  adaptive thinking, structured outputs via `messages.parse`). Set `ANTHROPIC_API_KEY` or
  `AI_API_KEY` and it takes over from the deterministic provider.
- The division of labour is deliberate and enforced by the system prompt: the deterministic
  engine decides weekly volume, phase and available days; the model reshapes workout structure,
  session spacing, intensity distribution and rationale within that envelope. It is told not to
  invent load, threshold or zone numbers, not to add or remove sessions, and not to move work
  onto days the seed did not offer.
- Only a narrow context slice is sent: state, block, objectives, profile, constraints, six weeks
  of summaries. Raw streams, full history and stored thresholds never leave the application.
- A `stop_reason: "refusal"` is checked explicitly - it arrives as HTTP 200, not an exception -
  and raises so the caller falls back to the validated deterministic seed.
- Every model command is still re-validated by `validate_commands` before it can become a
  proposal, and again on approval. Tests assert a model cannot delete a locked workout, backdate
  a session, or bypass the fallback.


## v0.7.0 - Actual planning

The app could create workouts but it could not plan. Objectives, training blocks and
workouts all existed with nothing connecting them: blocks were hand-picked with arbitrary
dates, the planner generated one week at a time re-deriving the same target from the same
8-week median, and a block's `weekly_load` target was read by nothing at all.

### Added

- **Periodisation** (`app/planning/season.py`, `POST /api/v1/coach/plan-season`). Works
  backwards from the objective date to decide how many weeks of base, build, specific, peak
  and taper fit, and what weekly load each week carries. Ramps from the athlete's own recent
  load at up to 7% per week - below the projection engine's 15% spike threshold, so a plan
  never generates its own warning - capped at 1.45x established load. Every fourth loading
  week is a recovery week. Long objectives get a two-week taper. Short horizons collapse
  gracefully: with three weeks left the plan is peak plus taper, not a base phase.
  Previews by default; blocks are only created with `apply`.
- **Multi-week generation** (`POST /api/v1/coach/generate-block`). Fills up to 16 weeks in one
  proposal, each week shaped by its own periodised target, so progression, recovery weeks and
  the taper survive. Later weeks see what earlier weeks scheduled and do not double-book.
- **General-fitness weeks.** "Plan my next week" with no race and no block now states an
  intent - recover, maintain or build - chosen from current form and recent load, and explains
  why. Overridable explicitly. Previously a targetless week silently reused the race template.
- **`GET /api/v1/analytics/block-progress`**: target vs planned vs actual per week for every
  block. The one view where objective, block, calendar and completed training meet.

### Fixed

- A block's `targets.weekly_load` now affects the plan. The Season UI wrote it, the planner
  read only `weekly_hours`, so the number an athlete typed in did nothing.
- `foundation` blocks had no phase factor, so they behaved exactly like having no block.


## v0.6.1 - Visual identity and interface pass

- **App icon.** `app/icon.svg`: a ridgeline that doubles as a load curve. Three strokes and
  one solid peak, so it survives 16px with no text and no gradient. The sidebar uses the same
  mark via `Logo`.
- **Real icons.** Hand-written 16px SVG paths replace the emoji in the calendar. Emoji render
  differently on every platform, cannot inherit colour, and were the main thing making the UI
  look improvised.
- **Chrome is monochrome; colour belongs to data.** The primary action is now near-white on
  dark rather than a saturated indigo, and the only coloured piece of chrome is the logo mark.
  A saturated accent button competing with the charts was both a template tell and a
  legibility problem in a dense data tool.
- **Tighter geometry.** Radii 3-9px instead of 8-16px, hairline borders, shadows removed from
  cards, denser type scale and spacing. Sport identity in the calendar is a 2px left rule
  rather than a filled block.
- **Numbers get a monospace face** with tabular figures, so values stop reflowing between
  updates and columns line up.
- **Neutral surfaces.** The indigo/purple cast is gone in favour of neutral graphite. Series
  hues were re-run through the colourblind and contrast validator against the new surface and
  still pass: fitness/fatigue/form on all pairs, the load stack on adjacent pairs.
- Flat background: the radial gradient was decoration competing with the charts.
- Dashboard restructured around stat tiles with context, plus a week summary strip.
- Date ranges use a plain hyphen rather than an arrow.


## v0.6.0 - Power profile, race predictions, and a real design system

### Added

- **Power profile** (`/power`, `GET /api/v1/power/profile`). Mean-maximal power curve on a
  log-duration axis, key-effort tiles, full efforts table with period-over-period change,
  critical power and W' fitted from the curve, and rider-type classification. Per-activity
  bests are computed once at ingest and stored on the metrics row, so a multi-year curve is
  an aggregation rather than a re-read of every stream.
- **Critical power** fitted with the two-parameter model in linear form (P = W'/t + CP) over
  2-20 minute efforts only, since the model over-predicts short efforts and under-predicts
  long ones outside that band. Reports points used and confidence.
- **Rider type** from curve *shape* rather than absolute height, expressed as ratios against
  the athlete's own 5-minute power, so it needs no body weight.
- **Running race predictions** (`GET /api/v1/running/predictions`) for 5K, 10K, half and full
  marathon. Two methods reported side by side rather than blended: Riegel extrapolation from
  the closest recorded effort, and critical speed from threshold pace. Confidence is driven by
  how far the prediction reaches past the evidence, so a marathon predicted from a 10K is
  explicitly labelled an extrapolation. Trail running is excluded because terrain makes raw
  pace meaningless as race evidence.
- **Weight** as a time-versioned threshold (`metric: weight_kg`) rather than a new column, so
  W/kg stays correct for historical activities and no migration is needed.
- `POST /api/v1/strava/sync?after_days=N` scopes a backfill. With streams enabled each
  activity costs an extra API call, and Strava allows roughly 100 per 15 minutes.

### Changed

- **Design system.** `globals.css` is now token-based: surfaces, ink, status, spacing, radii
  and chart colour defined once and referenced by role. Adds an active nav state, a proper
  type scale, tabular numerals throughout, hover states, and restyled tables, cards,
  calendar, timeline and warnings.
- **Chart colours are validated, not chosen by eye.** Series hues were run through the
  colourblind/contrast validator against this theme's actual surface. Fitness (blue), fatigue
  (magenta) and form (yellow) pass on all pairs; the four load-stack slots pass on adjacent
  pairs, which is the pairlist that applies to stacked bars. A blue/violet pairing for the
  power-curve comparison was rejected by the validator and replaced with a muted dashed
  neutral, which is also the more honest encoding: a previous period is a reference, not a
  second category.
- Charts gained legends (identity no longer rests on colour alone), a gradient fill under
  fitness, dashed form, a 2px surface gap between stacked segments, active dots, and a
  today marker on the projection.
- Threshold estimation and the power curve share one duration-weighted rolling-window
  implementation. Sample durations are derived once per stream instead of once per window,
  which cut curve computation roughly threefold.


## v0.5.3 - Load correctness, validated against a real history

Found by replaying a real 2,767-activity history (2012-2026) through the metrics rather
than by reading code. Measured effect of this release on that history:

```text
total training load   238,400  ->  171,567   (-28%)
single worst activity   8,920  ->      806
activities with descent load        0  ->  1,100
```

### Fixed

- **Metabolic load used elapsed time instead of moving time.** Strava computes
  `weighted_average_watts` and `average_heartrate` over moving time, so multiplying either
  by elapsed time inflated load by the whole stopped fraction. On the reference history 45%
  of activities had elapsed > 1.25x moving and 7% exceeded 5x. One ride left recording for
  three days scored 8,920 load - a quarter of a normal training year in a single day, which
  corrupted fitness, fatigue and form for months around it and poisoned the 7- and 28-day
  totals the AI coach reasons over. `resolve_durations` now splits the two: metabolic load
  uses moving time, time-on-feet keeps elapsed but is clamped at 3x moving so a forgotten
  stop cannot masquerade as a 72-hour effort.
- **Descent load was inert on every summary-imported activity.** Strava's summary payload
  has `total_elevation_gain` and no loss field, and loss was only derivable from streams.
  With `STRAVA_SYNC_STREAMS` off by default that left `elevation_loss_m` null everywhere, so
  `descent_load` was 0 across an entire history and v0.5's headline descent weighting
  contributed nothing at all. Loss is now estimated from gain using the summary's own
  start/end coordinates to detect a closed loop, always labelled with its basis
  (`estimated_closed_loop` / `estimated_open_route` / `stream`) and confidence.
- **A completed backfill reported every activity as failed.** In `historical_sync`, a
  successful `IntegrityError` retry that imported a new activity fell through to
  `failed_count` because only the duplicate branch continued. Sessions showed
  `imported=0 failed=2770` with 2,767 activities sitting in the table.
- **An interrupted sync blocked all later ones.** A session left in `processing` was still
  considered active forever, so the already-running guard refused every new sync.
  `reap_stale_sessions` retires sessions with no progress for six hours.
- **Threshold estimation selected efforts by elapsed time,** letting a short ride with a
  long cafe stop qualify as a sustained effort. Candidate selection now uses working time.
- **Trail runs were being scored with road weights.** Strava reports most trail runs as
  plain `Run`. On the reference history road runs sat at p95 = 21 m/km while activities
  Strava did label `TrailRun` began at p10 = 31 m/km, so an explicit running label is now
  refined to `trail_running` above 30 m/km over at least 3 km, at medium confidence so it
  stays overridable.

### Added

- `GET /api/v1/activities/duplicates` surfaces probable repeat recordings of one session.
  The fingerprint buckets distance to 20 m, tighter than GPS variance on a re-recorded
  route, so 34 pairs differing by 1-4% were both stored and counted twice. They are never
  merged automatically - each has its own provider id, so which copy to keep is the
  athlete's decision - and `DELETE /api/v1/activities/{id}` resolves them.
- Threshold responses now include `advice`: what is missing, what it costs, and whether it
  can be estimated at all. Resting HR can only be inferred from an HR stream, so a
  summary-only history cannot produce it automatically, and 37% of activities were silently
  falling back to a duration-based guess without saying why.
- `details.durations` on every activity records the metabolic and durability basis used,
  and whether stopped time was clamped.


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
