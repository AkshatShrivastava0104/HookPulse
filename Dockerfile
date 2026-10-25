# Multi-stage production Dockerfile
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11-slim AS runner

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create non-root user and directory
RUN useradd -m -u 1000 appuser && \
    mkdir -p /data && chown -R appuser:appuser /data /app

COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser tests/ ./tests/
COPY --chown=appuser:appuser pytest.ini .

USER appuser

ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATABASE_URL=sqlite:////data/app.db

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
