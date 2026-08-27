# Endurance AI Platform

Training-first endurance analytics, history ingestion and adaptive planning foundation.

## v0.2 major slice

This version adds the first production-shaped integration and ingestion layer on top of v0.1:

- Strava OAuth2 connect/callback flow
- encrypted access and refresh token storage
- automatic token refresh
- paginated historical Strava backfill
- rate-limit-aware backfill that can pause and resume
- Strava webhook ingestion for activity create/update/delete events
- Strava deauthorization handling and explicit disconnect endpoint
- asynchronous Celery + Redis import workers
- durable staging for FIT/GPX/TCX/ZIP uploads before background processing
- live import progress polling in the web UI
- source-aware deduplication through one canonical activity model
- stream-derived normalized power when FIT streams contain power
- HR and power zone time calculation when historical thresholds exist
- aerobic decoupling calculation when stream quality is sufficient
- weekly load/volume/elevation/mechanical-load analytics endpoint
- richer activity API exposing metric details and load provenance

The key rule remains: Strava is an adapter. Activities are imported into the platform's canonical model and all analytics operate from that model.

## Architecture

```text
Strava OAuth/Webhooks        FIT / GPX / TCX / ZIP
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
                  Fitness analytics
```

Large work is pushed to Celery workers through Redis when `ASYNC_TASKS=true`. For simple local development, `ASYNC_TASKS=false` executes the same task code eagerly without requiring a worker.

## Quick start with Docker

1. Copy environment values and configure a Strava application if you want Strava sync:

```bash
export APP_SECRET='use-a-long-random-secret'
export STRAVA_CLIENT_ID='...'
export STRAVA_CLIENT_SECRET='...'
export STRAVA_WEBHOOK_VERIFY_TOKEN='another-random-secret'
```

2. Start the stack:

```bash
docker compose up --build
```

- Web: http://localhost:3000
- API: http://localhost:8000
- API docs: http://localhost:8000/docs

The Docker stack contains web, API, PostgreSQL, Redis and a Celery worker.

## Strava setup

Configure the Strava application callback domain for your API host and set:

```env
STRAVA_REDIRECT_URI=http://localhost:8000/api/v1/strava/callback
STRAVA_FRONTEND_REDIRECT_URI=http://localhost:3000/imports?strava=connected
```

For local development, OAuth callbacks work against localhost, but Strava webhooks need a publicly reachable HTTPS endpoint. Once your API is exposed publicly, create the application's webhook subscription from the backend directory:

```bash
python scripts/create_strava_webhook.py https://your-api.example.com/api/v1/strava/webhook
```

Only one webhook subscription is required per Strava application. Events are routed to the correct local athlete through Strava's `owner_id`.

Historical backfill intentionally uses the paginated `/athlete/activities` summaries instead of issuing one detailed request per historical activity. This makes importing several years of history feasible within API limits. New or updated activities arriving through webhooks use the detailed activity endpoint. Set `STRAVA_SYNC_STREAMS=true` if you also want the webhook path to retrieve detailed streams; leave it false initially to keep API usage conservative.

## File import

The Imports screen accepts:

- `.fit`
- `.gpx`
- `.tcx`
- `.zip` containing supported files

Uploads are first staged on durable storage, then queued. ZIP entries are validated for path traversal, excessive file counts and decompressed size before parsing. One malformed activity does not fail the whole archive.

## Metrics implemented

Training load uses the best available deterministic method:

1. normalized power + time-versioned FTP/critical power
2. average power + threshold
3. HR TRIMP-like load + resting/max HR
4. duration/session-RPE fallback

Stream enrichment currently calculates:

- normalized power from approximately 1 Hz power streams
- power-zone seconds against the threshold valid on the activity date
- HR-zone seconds against the threshold HR valid on the activity date
- aerobic decoupling using power/HR, falling back to speed/HR
- mountain mechanical load with descent weighted more heavily than ascent

Metric details and confidence are persisted alongside the metric version so later formulas can be reprocessed and audited.

## Useful API endpoints

```text
GET  /api/v1/health
GET  /api/v1/activities
POST /api/v1/imports/files
GET  /api/v1/imports
GET  /api/v1/imports/{id}

GET  /api/v1/strava/connect
GET  /api/v1/strava/callback
GET  /api/v1/strava/status
POST /api/v1/strava/sync
POST /api/v1/strava/disconnect
GET  /api/v1/strava/webhook
POST /api/v1/strava/webhook

GET  /api/v1/analytics/fitness
GET  /api/v1/analytics/weekly
GET  /api/v1/calendar
GET  /api/v1/objectives
POST /api/v1/objectives
POST /api/v1/planned-workouts
```

## Tests

From `backend/`:

```bash
pytest -q
```

The current suite covers base load formulas, fitness EWMA, stream normalized power, zones, decoupling and signed OAuth state round-tripping.

## Important limitations / next slice

This is still an implementation foundation, not a complete training product. The next high-value slice should be the planning engine:

- real drag/drop training calendar
- structured workout builder
- plan-vs-actual matching
- future fitness/fatigue/form projections
- objective and block editor
- planning constraints and validation
- athlete context builder for the later AI coach

The AI layer should still come after those deterministic planning primitives exist.
