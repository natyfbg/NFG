#!/bin/sh
set -eu

# Compute values without relying on inline expansion inside the gunicorn arg
PORT="${PORT:-5000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-3}"
GUNICORN_THREADS="${GUNICORN_THREADS:-2}"

exec gunicorn \
  -w "$WEB_CONCURRENCY" \
  -k gthread --threads "$GUNICORN_THREADS" \
  -b "0.0.0.0:${PORT}" \
  app:app
