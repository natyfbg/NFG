# NFG

NFG is a Flask + Jinja + MongoDB fitness application moving toward a structured fitness SaaS. The current product centers on:

- program-first public flow
- workout library and exercise guidance
- mobile-first UI shell
- admin workflow for workouts, programs, weeks, and week items

## Repo Layout

- `backend/`
  - Flask application, templates, static assets, Docker files, and seed script
- `render.yaml`
  - Render deployment definition
- `instance/`
  - local runtime artifacts only; not for source control

## Environment Files

Local development expects environment files inside `backend/`.

- Example file: `backend/.env.example`
- Local file: `backend/.env`
- Do not commit real secrets

Minimum local variables:

```env
MONGO_URI=mongodb://localhost:27017/NFG
SECRET_KEY=replace-me
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH='...'
```

Notes:

- `ADMIN_PASSWORD_HASH` is preferred over `ADMIN_PASSWORD`.
- If you use Docker Compose with `backend/.env`, escape each `$` in `ADMIN_PASSWORD_HASH` as `$$`.
- `app.py` converts `$$` back to `$` before hash validation.
- Hosted environments should provide `SECRET_KEY` and `ADMIN_PASSWORD_HASH` explicitly.

## Local Python Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python seed.py
python app.py
```

## Docker Compose

```bash
cd backend
docker compose up --build
```

Services:

- app: `http://localhost:5050`
- mongo: `mongodb://localhost:27017`

Compose expects:

- `backend/docker-compose.yml`
- `backend/.env`

The app container now boots through `/bin/sh /app/starter.sh`, which keeps Docker Compose and Render aligned and avoids bind-mount executable-bit issues locally.

## Render Deployment

Render uses:

- `render.yaml`
- `backend/Dockerfile`
- `backend/starter.sh`

Required hosted env vars:

- `MONGO_URI`
- `SECRET_KEY`
- `ADMIN_PASSWORD_HASH`

Optional:

- `MONGO_DB`
- `ADMIN_USERNAME`

Uploads are mounted to a Render disk at `/app/static/uploads`.

Health endpoint:

- `/healthz`

## Branch / Developer Workflow

- Treat `mac-dev` as an active development branch.
- Keep environment files local only.
- Do not commit runtime logs, virtualenvs, local Mongo data, or uploads.
- Prefer small, isolated deployment/config changes over mixed product changes.

## Deployment Safety Notes

- Public behavior should not depend on `.env` values committed to the repo.
- Render should override secrets and Mongo connection settings.
- Docker and Render should share the same startup path where possible.
- Runtime logs belong in ignored local paths, not source control.
