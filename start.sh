#!/bin/sh
set -e

echo "=== Running database migrations ==="
timeout 15 python -m src.db.migrate || echo "WARNING: Migrations failed or timed out, continuing anyway..."

echo "=== Starting gunicorn on port ${PORT:-8000} ==="
exec gunicorn src.server:app --preload -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8000} --timeout 120
