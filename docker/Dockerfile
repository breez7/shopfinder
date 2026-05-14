# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    SHOPFINDER_DB_PATH=/data/shopfinder.db

WORKDIR /app

# --- builder ----------------------------------------------------------------
FROM base AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY README.md ./
RUN pip install --prefix=/install .

# --- runtime ----------------------------------------------------------------
FROM base AS runtime

# Copy installed site-packages
COPY --from=builder /install /usr/local

# Copy application source (templates, static assets, modules)
COPY app ./app
COPY README.md ./

RUN mkdir -p /data && useradd --uid 1000 --create-home shopfinder \
    && chown -R shopfinder:shopfinder /data /app
USER shopfinder

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
