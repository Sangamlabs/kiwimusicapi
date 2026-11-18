# Multi-stage Dockerfile for Kiwi Music API (Rust Streamer + Python FastAPI)

# -------------------------------------------------------------
# Stage 1: Build high-performance Rust streaming engine
# -------------------------------------------------------------
FROM rust:1.85-slim AS rust-builder

WORKDIR /usr/src/rust-streamer

COPY backend/rust-streamer/Cargo.toml backend/rust-streamer/Cargo.lock ./
COPY backend/rust-streamer/src ./src

RUN cargo build --release --locked --bin rust-streamer

# -------------------------------------------------------------
# Stage 2: Final lightweight runtime container
# -------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    RUST_MEDIA_PORT=8088 \
    RUST_MEDIA_ENGINE_URL=http://127.0.0.1:8088

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install compiled Rust binary
COPY --from=rust-builder /usr/src/rust-streamer/target/release/rust-streamer /usr/local/bin/rust-streamer
RUN chmod +x /usr/local/bin/rust-streamer

# Install Python requirements
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend application source
COPY backend/ .
RUN chmod +x start.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/v1/health || exit 1

CMD ["./start.sh"]
