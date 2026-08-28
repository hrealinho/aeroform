# Aeroform Platform

Training-first endurance analytics, season planning and grounded AI coaching. The product deliberately keeps social mechanics out of the core experience: activities become private training data used for analytics, projections and adaptive planning.

## v0.5: sport-aware terrain load and classification

v0.5 replaces the single mountain-load heuristic with a transparent multi-dimensional load profile. The primary `training_load` used by fitness/fatigue now becomes a sport-specific composite, while metabolic, mechanical, ascent, descent and durability components remain individually inspectable.

Key additions:

- trail running and hiking load explicitly account for **distance, ascent, descent and time-on-feet**
- descent has its own eccentric/downhill load and is weighted more strongly than ascent for trail/mountain sports
- overall load blends metabolic and terrain stress rather than blindly adding an elevation bonus
- road running remains primarily metabolic, with a smaller mechanical contribution
- hiking/mountaineering down-weight very long HR-TRIMP values and give more influence to terrain/time-on-feet
- FIT sport/sub-sport mapping now distinguishes trail running from road running
- ambiguous GPX files use conservative speed/elevation-density classification with confidence/reason metadata
- activity classification can be manually overridden; metrics are recalculated immediately
- GPX extensions can contribute HR/cadence; GPX/TCX elevation gain/loss is estimated from a smoothed altitude stream
- Strava stream imports can derive elevation loss when it is absent from the summary payload
- weekly analytics expose ascent, descent, metabolic, mechanical and durability load
- the dashboard includes a load explorer for overall/metabolic/mechanical/ascent/descent/durability fitness curves
- Athlete Context and Ask Coach now include descent and mechanical-load evidence

### v0.5 load model

The metabolic component still uses the best available physiological method: power, HR/TRIMP, then session-RPE. Terrain-aware sports additionally calculate:

```text
distance load
ascent load
descent / eccentric load
durability / time-on-feet load
            |
            v
     mechanical load
```

The overall score uses explicit sport-specific blending. For example:

```text
road running   = 0.90 * metabolic + 0.18 * mechanical
trail running  = 0.80 * metabolic + 0.40 * mechanical
hiking         = 0.60 * metabolic + 0.65 * mechanical
mountaineering = 0.55 * metabolic + 0.75 * mechanical
cycling        = metabolic
```

These are **v0.5 starting coefficients**, not claims of physiological truth. Every component and blend weight is retained in metric details so the model can be validated against real histories and versioned later.

If upgrading an existing local database, recalculate old activities without re-importing raw files:

```bash
cd backend
python scripts/recompute_metrics.py
```

## v0.4: grounded AI coach and adaptive planner

v0.4 adds the first end-to-end AI planning loop on top of the v0.3 calendar and projection engine:

- compact Athlete Context built from derived metrics, objectives, constraints, preferences and plan state
- persistent Ask Coach analysis with evidence returned separately from model judgement
- weekly plan generation from objectives, recent capacity, current training block and availability
- current-week adaptation after unexpectedly large load or missed sessions
- structured AI commands: create, update, move and delete planned workouts
- deterministic command validation before any proposal can touch the calendar
- approval/rejection workflow; AI changes are never silently applied
- locked workouts are immutable to the AI
- projection checks compare the current plan with the proposed plan
- AI plan-change audit records before/after state and reasoning
- "Why this session?" explanations for planned workouts
- coach preferences for available hours, long-session day, rest day and doubles
- provider abstraction: deterministic local provider by default, optional vendor-neutral JSON AI gateway

The key architecture rule is unchanged: **the LLM is not the metrics engine or the planning rules engine**. It can reason over structured context and propose commands, but the application validates those commands before they become a pending proposal and validates them again when the user approves.

## Strava export compatibility discovered with real history

v0.4 also hardens bulk import against the format used by real Strava exports. In addition to plain FIT/GPX/TCX, archives may contain:

- `.fit.gz`
- `.gpx.gz`
- `.tcx.gz`
- macOS `__MACOSX/._*` resource-fork metadata if the export folder was re-zipped on a Mac

The importer now recognizes gzip-compressed activity files, keeps the original compressed file as the immutable raw source, decompresses only into a temporary parser input, and ignores macOS metadata files rather than trying to parse them as activities.

You can inspect an archive before importing it:

```bash
cd backend
python scripts/inspect_activity_archive.py /path/to/activities.zip
```

## Architecture

```text
Strava OAuth/Webhooks       FIT / GPX / TCX / ZIP / *.gz
        |                              |
        v                              v
  Strava adapter                 Import session
        |                              |
        +------------+-----------------+
                     v
              Canonical Activity
              /        |       \
        provenance   streams   metrics
                     |          |
                     v          v
              stream metrics  training load
                     \          /
                      v        v
                   Athlete Context
                         |
             +-----------+-----------+
             |                       |
             v                       v
       Training Analyst       Planning Engine
             |                       |
             +-----------+-----------+
                         v
                    AI Provider
                         |
                         v
               Structured commands
                         |
                         v
             Deterministic validator
                         |
                   pending proposal
                         |
                   user approves
                         |
                         v
                      Calendar
```

Large import/sync work is pushed to Celery workers through Redis when `ASYNC_TASKS=true`. For simple local development, `ASYNC_TASKS=false` executes the same task code eagerly without requiring a worker.

## Quick start with Docker

```bash
export APP_SECRET='use-a-long-random-secret'
# Optional Strava integration
export STRAVA_CLIENT_ID='...'
export STRAVA_CLIENT_SECRET='...'
export STRAVA_WEBHOOK_VERIFY_TOKEN='another-random-secret'

docker compose up --build
```

- Web: `http://localhost:3000`
- API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`

The stack contains Next.js, FastAPI, PostgreSQL, Redis and a Celery worker.

## AI provider setup

No model credentials are required to run v0.5. The default provider is deterministic:

```env
AI_PROVIDER=local
```

This exercises the same Athlete Context, proposal, validation and approval architecture while keeping tests reproducible.

For a remote model, v0.5 exposes a small vendor-neutral HTTP JSON hook:

```env
AI_PROVIDER=http_json
AI_ENDPOINT=https://your-ai-gateway.example/v1/endurance
AI_API_KEY=...
AI_MODEL=your-model-name
```

The gateway receives structured context plus the deterministic draft/seed. It may return improved analysis text or plan commands. Returned commands are treated as **untrusted proposals** and must pass the deterministic validator. A provider failure falls back to the local grounded planner.

## Coach workflow

### Ask Coach

`POST /api/v1/coach/ask`

Questions are answered from structured metrics and return evidence separately. Examples:

- Why am I tired this week?
- Am I doing enough vertical?
- Compare the last four weeks with the previous four.
- How consistent has my plan adherence been?

### Generate next week

`POST /api/v1/coach/generate-week`

The planner considers:

- upcoming objectives
- current training block
- recent median weekly hours/load
- sport distribution
- explicit weekly time availability
- preferred long-session/rest days
- unavailable/max-hours constraints
- existing planned workouts

It produces a pending proposal, not calendar mutations.

### Adapt this week

`POST /api/v1/coach/adapt-week`

The first deterministic adaptation rules protect recovery after unexpectedly large recent load, reduce low-priority volume when current-week actual + future planned load is well above recent norms, and explicitly avoid making up missed past sessions.

### Apply a proposal

```text
AI/provider
    -> commands
    -> schema validation
    -> ownership/lock/date/availability checks
    -> projected-load warnings
    -> pending AIProposal
    -> user approval
    -> re-validation against current calendar
    -> atomic calendar mutations + audit
```

## File import

The Imports screen accepts:

- `.fit`, `.fit.gz`
- `.gpx`, `.gpx.gz`
- `.tcx`, `.tcx.gz`
- `.zip` containing any supported combination, including nested folders

ZIP entries are checked for traversal, excessive member count, decompressed size and oversized individual files. One malformed activity does not fail the whole archive. Raw files are stored under an athlete-scoped path so identical file hashes from different SaaS tenants do not share provenance records.

## Metrics implemented

The metabolic load uses the best available deterministic method:

1. normalized power + time-versioned FTP/critical power
2. average power + threshold
3. HR TRIMP-like load + resting/max HR
4. duration/session-RPE fallback

Stream enrichment calculates normalized power, power-zone time, HR-zone time, aerobic decoupling, smoothed elevation gain/loss, grade distribution and vertical rates. Running/trail/hiking/mountaineering then add a versioned terrain profile with distance, ascent, descent and durability components. Fitness/fatigue/form use the composite `training_load`; each component can also be charted independently through `load_kind`.

## Main v0.5 API endpoints

```text
GET  /api/v1/coach/context
GET  /api/v1/coach/profile
PUT  /api/v1/coach/profile
POST /api/v1/coach/ask
GET  /api/v1/coach/messages
POST /api/v1/coach/generate-week
POST /api/v1/coach/adapt-week
GET  /api/v1/coach/proposals
POST /api/v1/coach/proposals/{id}/approve
POST /api/v1/coach/proposals/{id}/reject
GET  /api/v1/planned-workouts/{id}/why

GET  /api/v1/calendar
GET  /api/v1/analytics/projection
POST /api/v1/matching/auto
POST /api/v1/planned-workouts
PATCH /api/v1/planned-workouts/{id}

POST /api/v1/imports/files
GET  /api/v1/imports/{id}
GET  /api/v1/strava/connect
POST /api/v1/strava/sync
```

## Tests

From `backend/`:

```bash
pytest -q
```

v0.5 includes terrain-load/classification tests in addition to tests for grounded fatigue analysis, structured week generation, unavailable-day handling, AI command schema validation, and Strava-style gzip archive discovery/macOS metadata filtering, in addition to the existing load, fitness, stream, OAuth and planning tests.

## Still intentionally missing

v0.4 is the first adaptive-coach slice, not a finished SaaS. High-value next work includes:

- richer athlete model and automatically inferred threshold history
- running GAP, pace zones and efficiency trends
- cycling power curve / best efforts / FTP history
- sport-specific fitness and calibrated cross-sport transfer
- better trail/downhill mechanical-load model
- richer plan adherence and missed-workout semantics
- full-season hierarchical generation (blocks -> weekly targets -> weeks -> workouts)
- custom AI-generated chart queries
- production authentication and tenant identity instead of the current demo athlete helper
- Alembic migrations, S3-compatible storage, GDPR workflows, observability and billing

The product specification remains in `docs/product-requirements.docx`.
