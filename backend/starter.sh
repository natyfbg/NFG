#!/bin/sh
set -e
echo "Starting NFG app..."
exec gunicorn -w 3 -b 0.0.0.0:8000 app:app --timeout 120
