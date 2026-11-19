#!/bin/bash
set -e

echo "[STARTUP] Starting Kiwi Music API Hybrid Architecture..."

# 1. Run database migrations
echo "[STARTUP] Applying database migrations..."
alembic -c alembic.ini upgrade head || echo "[STARTUP] Warning: Alembic upgrade skipped or already up to date"

# 2. Start Rust Media Engine on internal loopback port
RUST_PORT="${RUST_MEDIA_PORT:-8088}"
echo "[STARTUP] Launching Rust Media Engine on port ${RUST_PORT}..."
if [ -x "/usr/local/bin/rust-streamer" ]; then
    /usr/local/bin/rust-streamer serve --port "${RUST_PORT}" &
    RUST_PID=$!
    echo "[STARTUP] Rust Media Engine PID: ${RUST_PID}"
else
    echo "[STARTUP] Notice: /usr/local/bin/rust-streamer not found. Running in Python-standalone fallback mode."
    RUST_PID=""
fi

# Cleanup handler for graceful shutdown
cleanup() {
    echo "[SHUTDOWN] Stopping services..."
    if [ -n "$RUST_PID" ]; then
        kill -TERM "$RUST_PID" 2>/dev/null || true
    fi
    if [ -n "$UVICORN_PID" ]; then
        kill -TERM "$UVICORN_PID" 2>/dev/null || true
    fi
    exit 0
}

trap cleanup TERM INT

# 3. Start Python FastAPI via Uvicorn in foreground with multi-worker concurrency
WEB_PORT="${PORT:-8000}"
WORKERS="${WEB_CONCURRENCY:-2}"
echo "[STARTUP] Launching Python FastAPI on port ${WEB_PORT} with ${WORKERS} workers..."
uvicorn app.main:app --host 0.0.0.0 --port "${WEB_PORT}" --workers "${WORKERS}" --limit-concurrency 1000 --backlog 2048 &
UVICORN_PID=$!

wait "$UVICORN_PID"
cleanup
