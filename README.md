# HookPulse - Production-Grade Webhook Ingestion & Reliability Engine

[![CI/CD](https://github.com/AkshatShrivastava0104/CP-/actions/workflows/ci.yml/badge.svg)](https://github.com/AkshatShrivastava0104/CP-/actions/workflows/ci.yml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109.0-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg?logo=postgresql)](https://www.postgresql.org)
[![Prometheus](https://img.shields.io/badge/Prometheus-v2.50-e6522c.svg?logo=prometheus)](https://prometheus.io)
[![Grafana](https://img.shields.io/badge/Grafana-10.3-f46800.svg?logo=grafana)](https://grafana.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A high-throughput, secure, and resilient FastAPI backend engine for ingesting and processing WhatsApp-like webhook messages. Built with HMAC-SHA256 authentication, idempotent message storage, PostgreSQL connection pooling, async worker forwarding with exponential backoff & dead-letter queue (DLQ), Prometheus metrics, pre-provisioned Grafana dashboards, and k6 load testing scripts.

---

## Architecture Overview

```mermaid
flowchart TD
    Client[Webhook Producer / Client] -->|POST /webhook + X-Signature| API[HookPulse FastAPI Engine]
    
    subgraph Security Layer
        API --> AuthDep[FastAPI Auth Dependency]
        AuthDep --> HMAC[HMAC-SHA256 Constant-Time Check]
        HMAC -->|Invalid / Missing| R401[401 Unauthorized]
        HMAC -->|Valid| Schema[Pydantic Schema Validation]
        Schema -->|Invalid| R422[422 Unprocessable Entity]
    end

    subgraph Storage Layer
        Schema -->|Valid| DB[Dual Storage Engine]
        DB -->|Connection Pool| PG[(PostgreSQL 16)]
        DB -->|Fallback / Local| Lite[(SQLite WAL Mode)]
        PG -->|Duplicate PK| R200Dup[200 OK duplicate: true]
        PG -->|New Insert| R200OK[200 OK created: true]
    end

    subgraph Async Reliability Engine
        R200OK --> Worker[DeliveryWorker Queue]
        Worker -->|HTTP POST| Target[Downstream Webhook Target]
        Target -->|5xx or Timeout| Backoff[Exponential Backoff + Jitter]
        Backoff -->|Retry attempts < max| Worker
        Backoff -->|Exhausted max retries| DLQ[Dead Letter Queue DLQ]
    end

    subgraph Observability
        Prom[Prometheus] -->|Scrapes /metrics 5s| API
        Grafana[Grafana Dashboard] -->|Queries 9090| Prom
    end
```

---

## Key Features

1. **Hardened HMAC-SHA256 Security**:
   - Authentication checked **before** body parsing or schema validation via FastAPI dependencies.
   - Timing-safe verification using `hmac.compare_digest`.
   - Complete removal of backdoors/bypasses (`demo-signature` vulnerability eliminated).
   - Missing or forged signatures fail fast with `401 Unauthorized`.

2. **Idempotency & Deduplication**:
   - `message_id` acts as the primary key.
   - Concurrent duplicate inserts return `200 OK` with `dup: true` without raising database integrity errors to the client.

3. **Dual Storage Engine (PostgreSQL + SQLite)**:
   - First-class production support for **PostgreSQL 16** via `psycopg` v3 with `ConnectionPool`.
   - Seamless local development and testing support for **SQLite** with Write-Ahead Logging (WAL) and `busy_timeout=5000` to prevent database locks.
   - Compound indexes: `idx_from_msisdn`, `idx_ts`, `idx_ts_message_id` for $O(\log N)$ filtered pagination and aggregation.

4. **Async Delivery Workers & Retries**:
   - Outbound forwarding engine for downstream webhook consumers.
   - Configurable exponential backoff with randomized jitter to eliminate thundering herd problems.
   - Dead-letter queue (DLQ) tracking for failed deliveries.
   - Admin endpoints to monitor worker queue health and trigger manual replays (`POST /worker/deliveries/{id}/retry`).

5. **Observability Stack (Prometheus + Grafana)**:
   - Official Prometheus `/metrics` exposition format.
   - Request counters, histogram latency buckets ($5\text{ms}$ to $5000\text{ms}$), and worker job states.
   - Pre-configured Grafana datasource & dashboard auto-provisioned out of the box on port `3000`.

6. **k6 Load Testing Suite**:
   - Realistic load generation testing HMAC cryptographic verification at $200\text{--}500\text{+ RPS}$.
   - Validates latency percentiles ($p_{95} < 150\text{ms}$, $p_{99} < 300\text{ms}$) and error rates under stress.

7. **Interactive FastAPI Swagger UI**:
   - Interactive OpenAPI documentation available at `/docs` and ReDoc at `/redoc`.

---

## Quick Start with Docker Compose

### Prerequisites
- Docker & Docker Compose v2+
- Make (optional)

### 1. Configure Environment
```bash
cp .env.example .env
```
*(The default configuration points to PostgreSQL with `WEBHOOK_SECRET=testsecret`)*

### 2. Start Full Stack
Start the API, PostgreSQL, Prometheus, and Grafana:
```bash
make up
# Or: docker compose up -d --build
```

### 3. Verify Health Probes
```bash
# Liveness probe (200 OK if service is up)
curl http://localhost:8000/health/live

# Readiness probe (200 OK if database is connected and secret configured)
curl http://localhost:8000/health/ready
```

### 4. Access UIs
| Service | URL | Credentials |
|---------|-----|-------------|
| **FastAPI Swagger UI** | [http://localhost:8000/docs](http://localhost:8000/docs) | None |
| **FastAPI ReDoc** | [http://localhost:8000/redoc](http://localhost:8000/redoc) | None |
| **Prometheus UI** | [http://localhost:9090](http://localhost:9090) | None |
| **Grafana Dashboard** | [http://localhost:3000](http://localhost:3000) | `admin` / `admin` |

---

## Security Model: HMAC-SHA256 Verification

Every incoming webhook request to `POST /webhook` must include an `X-Signature` HTTP header.

### Specification:
1. **Algorithm**: `HMAC-SHA256(secret_bytes, raw_request_body_bytes)`
2. **Encoding**: Hexadecimal lowercase string (64 characters).
3. **Execution Order**:
   - The auth dependency executes **before** payload schema validation.
   - If `X-Signature` is missing or does not match the computed digest, the service rejects the request with HTTP `401 Unauthorized` without reading or storing the payload.
   - Any backdoor bypasses (such as `demo-signature`) have been strictly removed.

### Example in Python:
```python
import hmac, hashlib, json, requests

secret = "testsecret"
body = json.dumps({
    "message_id": "m100",
    "from": "+919876543210",
    "to": "+14155550100",
    "ts": "2025-01-15T10:00:00Z",
    "text": "Hello HookPulse!"
})

signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()

response = requests.post(
    "http://localhost:8000/webhook",
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-Signature": signature
    }
)
print(response.status_code, response.json())
```

---

## API Reference

### 1. `POST /webhook`
Ingests a webhook message.

- **Headers**:
  - `Content-Type: application/json`
  - `X-Signature: <hmac-sha256-hex>`
- **Request Body**:
  ```json
  {
    "message_id": "m1",
    "from": "+919876543210",
    "to": "+14155550100",
    "ts": "2025-01-15T10:00:00Z",
    "text": "Hello"
  }
  ```
- **Responses**:
  - `200 OK`: `{"status": "ok"}` (Message inserted or duplicate acknowledged)
  - `401 Unauthorized`: `{"detail": "invalid signature"}`
  - `422 Unprocessable Entity`: Validation failure on phone format or ISO timestamp

### 2. `GET /messages`
List and search stored messages with deterministic pagination.

- **Query Parameters**:
  - `limit` *(int, default=50, min=1, max=100)*: Result batch size
  - `offset` *(int, default=0, min=0)*: Pagination offset
  - `from` *(string, optional)*: Filter by sender phone number (e.g. `%2B919876543210`)
  - `since` *(ISO-8601 string, optional)*: Filter messages on or after timestamp
  - `q` *(string, optional)*: Substring search in message text
- **Response**:
  ```json
  {
    "data": [
      {
        "message_id": "m1",
        "from": "+919876543210",
        "to": "+14155550100",
        "ts": "2025-01-15T10:00:00Z",
        "text": "Hello"
      }
    ],
    "total": 1,
    "limit": 10,
    "offset": 0
  }
  ```

### 3. `GET /stats`
Get aggregated sender analytics and timestamp boundaries.

- **Response**:
  ```json
  {
    "total_messages": 1500,
    "senders_count": 42,
    "messages_per_sender": [
      { "from": "+919876543210", "count": 320 },
      { "from": "+14155550100", "count": 180 }
    ],
    "first_message_ts": "2025-01-10T09:00:00Z",
    "last_message_ts": "2025-01-15T12:00:00Z"
  }
  ```

### 4. `GET /worker/stats`
Inspect the status of async webhook delivery forwarding.

- **Response**:
  ```json
  {
    "total": 100,
    "pending": 2,
    "delivered": 95,
    "retrying": 1,
    "failed": 2
  }
  ```

### 5. `POST /worker/deliveries/{delivery_id}/retry`
Manually schedule a failed dead-letter delivery for re-processing.

### 6. `GET /metrics`
Prometheus metrics endpoint scraping request counters, status codes, latency histograms, and worker metrics.

---

## Monitoring: Prometheus & Grafana

The system ships with automated metrics and visualization:

1. **Prometheus Configuration** (`monitoring/prometheus/prometheus.yml`):
   - Scrapes `/metrics` every 5 seconds.
2. **Pre-provisioned Grafana Dashboard** (`monitoring/grafana/dashboards/hookpulse_dashboard.json`):
   - **Ingestion Breakdown**: Real-time graph of `created`, `duplicate`, `invalid_signature`, and `validation_error` events.
   - **Throughput (RPS)**: Request volume grouped by route and status.
   - **Latency Percentiles**: Live computed $p_{50}$, $p_{95}$, and $p_{99}$ latency graphs using Prometheus `histogram_quantile`.
   - **Worker Reliability**: Delivered vs. retrying vs. failed jobs.

---

## Load Testing with k6

High-throughput load tests are pre-written in `load_tests/k6_load_test.js`:

```bash
# Run the complete load test with real HMAC signature computation:
k6 run load_tests/k6_load_test.js

# Or run the quick smoke test:
k6 run load_tests/k6_smoke_test.js
```

### Performance & Load Testing Expectations:
- **Throughput**: Sustained 200–500 RPS on standard dual-core machines.
- **Latency**: $p_{95} \le 150\text{ms}$, $p_{99} \le 300\text{ms}$.
- **Idempotency**: 0 duplicate insertions during high-concurrency race conditions.

---

## Running Automated Tests

Run the full pytest suite (unit, integration, security, worker, and metrics tests):

```bash
# Inside docker
make test

# Or locally in virtual environment
pytest tests/ -v
```

All 25 tests pass out of the box with zero failures.

---

## Environment Variables Reference

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DATABASE_URL` | string | `postgresql://postgres:postgres@postgres:5432/hookpulse` | Connection string for PostgreSQL or SQLite (`sqlite:///data/app.db`) |
| `LOG_LEVEL` | string | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `WEBHOOK_SECRET` | string | *(Required)* | Secret key for computing and verifying HMAC signatures |
| `ENABLE_ASYNC_WORKER` | boolean | `true` | Enable or disable outbound delivery worker |
| `WEBHOOK_FORWARD_URL` | string | `""` | Optional downstream subscriber endpoint to forward webhooks |
| `WORKER_MAX_RETRIES` | int | `3` | Maximum retry attempts before moving to Dead-Letter Queue |
| `WORKER_BACKOFF_FACTOR` | float | `2.0` | Exponential backoff multiplier base |
| `WORKER_POLL_INTERVAL` | float | `1.0` | Seconds to wait between worker polling cycles |
| `RATE_LIMIT_RPS` | int | `100` | Rate limiter threshold |

---

## License

This project is licensed under the MIT License.
