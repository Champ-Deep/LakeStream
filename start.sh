#!/bin/sh
set -e

echo "=== Running database migrations ==="
timeout 30 python -m src.db.migrate || echo "WARNING: Migrations failed or timed out, continuing anyway..."

echo "=== Starting server on port ${PORT:-3001} ==="
exec uvicorn src.server:app --host 0.0.0.0 --port "${PORT:-3001}"
