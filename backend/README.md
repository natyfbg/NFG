# Backend Operations

This directory contains the Flask application, Docker assets, templates, static files, and seed tooling for NFG.

## Key Files

- `app.py` — main Flask app and configuration
- `requirements.txt` — Python dependencies
- `Dockerfile` — image build for local Docker and Render
- `docker-compose.yml` — local app + Mongo stack
- `starter.sh` — shared gunicorn startup path
- `.env.example` — safe local environment template

## Local Environment

Create your local env file here:

```bash
cp .env.example .env
```

Important:

- keep `.env` local only
- prefer `ADMIN_PASSWORD_HASH` over plaintext admin passwords
- if you use Docker Compose with `backend/.env`, escape each `$` in the hash as `$$`
- the app normalizes `$$` back to `$` at runtime

## Run Locally

### Python

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python seed.py
python app.py
```

### Docker

```bash
docker compose up --build
```

App URL:

- `http://localhost:5050`

## Deployment Notes

- Render builds from this directory’s `Dockerfile`
- startup goes through `/bin/sh /app/starter.sh`
- health endpoint is `/healthz`
- uploads are expected to persist outside the container in hosted environments
- **Production database is MongoDB Atlas.** `MONGO_URI` is supplied manually as a
  Render secret (`sync: false`), not derived from a Render-hosted Mongo service.
- **Local Docker development is unaffected** — `docker-compose.yml` still runs a
  local `mongo:7` container; only production points at Atlas.

## Current Safety Assumptions

- local runtime files should not be committed
- app secrets must come from environment variables in hosted environments
- Docker Compose is for development convenience, not production orchestration
