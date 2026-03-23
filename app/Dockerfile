# ── Stage 1: Builder — install dependencies
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build deps for psycopg2 / bcrypt native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime — lean final image
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app/ ./app/

# Non-root user for security
RUN useradd -m -u 1001 gateway && chown -R gateway:gateway /app
USER gateway

EXPOSE 8000

# Uvicorn: 1 worker in Docker (scale horizontally via container replicas)
# --no-access-log: we handle structured logging in middleware
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]