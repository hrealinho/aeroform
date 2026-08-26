# Endurance AI Platform

First implementation slice of the product requirements: a training-first endurance analytics and planning platform.

## Implemented now

- Canonical multi-source activity model
- FIT, GPX and TCX parsers
- ZIP bulk import with traversal/file-count/uncompressed-size protections
- Raw immutable file retention on local/S3-replaceable storage abstraction point
- Import sessions, progress counters and partial failure handling
- Duplicate fingerprinting and provenance preservation
- Versioned athlete thresholds
- Deterministic power, HR/TRIMP and session-RPE load calculations
- Mountain mechanical-load heuristic
- Fitness/fatigue/form EWMA engine
- Activity list API
- Fitness time-series API
- Objective and planned-workout models/APIs
- Calendar data endpoint
- Next.js dashboard with fitness chart
- Activities and file-import UI
- PostgreSQL/Redis-ready Docker Compose setup
- Unit tests for core metric formulas

## Intentionally not pretended complete

This repository is the first vertical slice, not the entire 18-page product in one commit. Still to implement includes Strava OAuth/webhooks/backfill, Celery-backed truly asynchronous imports, richer FIT stream calculations (normalized power/GAP/zones), calendar drag-and-drop, planned-vs-actual matching, projections, full season rules, and the AI coach orchestration layer.

## Run locally, fastest path

### Backend with SQLite

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Web: http://localhost:3000

### Full stack

```bash
docker compose up --build
```

## Recommended next implementation order

1. Strava OAuth, encrypted token storage, historical pagination and webhooks.
2. Celery jobs for imports/backfills/recalculation; SSE or polling progress.
3. Better stream-derived metrics: normalized power, zones, pace/GAP, decoupling, best efforts.
4. Planned-vs-actual matching and adherence.
5. Future fitness projection from planned load.
6. Season/block planner and constraint engine.
7. Athlete-context generator, validated planning commands, then LLM adapter.

The code is structured so Strava is an adapter and never becomes the domain model.
