#!/bin/sh
set -e

PORT="${PORT:-8000}"

echo "Starting NFG app on port ${PORT}..."

exec gunicorn -w 3 -b "0.0.0.0:${PORT}" app:app --timeout 120
