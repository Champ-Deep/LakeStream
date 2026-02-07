#!/bin/sh
set -e

echo "=== Running database migrations ==="
python -m src.db.migrate || echo "WARNING: Migrations failed, continuing anyway..."

echo "=== Starting uvicorn on port ${PORT:-8000} ==="
exec uvicorn src.server:app --host 0.0.0.0 --port "${PORT:-8000}"
