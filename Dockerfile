# Dockerfile for Frequenz FastAPI backend
# Target: Hetzner Cloud CX22 + Coolify (Nuremberg, DE)
#
# Multi-stage build to keep the runtime image small.

# ──────────────────────────────────────────────────────────────────────────
# Stage 1: builder — install Python deps into a venv
# ──────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for native wheels (numpy, Pillow, cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml ./

# Install into a venv we can copy to the runtime stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install deps directly from pyproject.toml — single source of truth for
# dependency versions (no manual list to keep in sync).
RUN pip install --upgrade pip && \
    python -c "import tomllib; \
deps = tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; \
print('\n'.join(deps))" > /tmp/requirements.txt && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# ──────────────────────────────────────────────────────────────────────────
# Stage 2: runtime — minimal image with the venv + source
# ──────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_ENVIRONMENT=production

# Runtime libs only — no compilers
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 frequenz

WORKDIR /app

# Copy the prebuilt venv
COPY --from=builder /opt/venv /opt/venv

# Copy application source
COPY --chown=frequenz:frequenz src ./src
COPY --chown=frequenz:frequenz data ./data
COPY --chown=frequenz:frequenz pyproject.toml ./

USER frequenz

EXPOSE 8000

# Healthcheck — Coolify uses this to gate restarts
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/api/pipeline-status || exit 1

# Run uvicorn directly. Single worker is fine for 5-50 concurrent users on a
# CX22 (2 vCPU); add more workers later via WEB_CONCURRENCY env var if needed.
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
