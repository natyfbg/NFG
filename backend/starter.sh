#!/bin/sh
set -e

PORT="${PORT:-8000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-3}"
GUNICORN_THREADS="${GUNICORN_THREADS:-2}"

printf 'Starting NFG app on port %s...\n' "$PORT"

exec gunicorn \
  -w "$WEB_CONCURRENCY" \
  -k gthread --threads "$GUNICORN_THREADS" \
  -b "0.0.0.0:${PORT}" \
  --timeout 120 \
  app:app
