#!/bin/sh
set -e

echo "=== Running database migrations ==="
timeout 30 python -m src.db.migrate || echo "WARNING: Migrations failed or timed out, continuing anyway..."

if [ "$SERVICE_MODE" = "worker" ]; then
    echo "=== Starting ARQ worker ==="
    exec python -m arq src.queue.worker.WorkerSettings
else
    echo "=== Starting API server on port ${PORT:-8080} ==="
    exec uvicorn src.server:app --host 0.0.0.0 --port "${PORT:-8080}"
fi
