#!/bin/sh
set -e

PORT="${PORT:-8000}"

echo "Starting NFG app on port ${PORT}..."

# Local Docker sets DEV_RELOAD=1: restart workers when Python files change (templates
# reload on their own). Render never sets it.
if [ "${DEV_RELOAD:-0}" = "1" ] && [ -z "${RENDER:-}" ]; then
  echo "Dev reload on: code changes are picked up automatically."
  exec gunicorn -w 2 -b "0.0.0.0:${PORT}" app:app --timeout 120 --reload
fi

exec gunicorn -w 3 -b "0.0.0.0:${PORT}" app:app --timeout 120
