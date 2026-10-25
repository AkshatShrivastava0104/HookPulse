import asyncio
import hmac
import hashlib
import uuid
import time
import os
import secrets
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Query, Header, Body, Depends, status
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler

from app.config import config
from app.models import WebhookMessage, MessagesListResponse, StatsResponse
from app.storage import Database
from app.logging_utils import setup_logging
from app.metrics import metrics
from app.worker import DeliveryWorker

logger = setup_logging(config.LOG_LEVEL)

if config.DATABASE_URL.startswith("postgresql://") or config.DATABASE_URL.startswith("postgres://"):
    db = Database(config.DATABASE_URL)
else:
    raw_db_path = config.DATABASE_URL.replace("sqlite:///", "")
    db_path = os.path.abspath(raw_db_path) if raw_db_path != ":memory:" else ":memory:"
    db = Database(db_path)

worker = DeliveryWorker(db)


def verify_signature(body: bytes, signature: Optional[str]) -> bool:
    if not signature or not config.WEBHOOK_SECRET:
        return False

    expected = hmac.new(
        config.WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


async def verify_webhook_auth(
    request: Request,
    x_signature: Optional[str] = Header(None, alias="X-Signature")
):
    if not x_signature:
        request.state.result = "invalid_signature"
        metrics.inc_webhook_request("invalid_signature")
        logger.error("Missing signature", extra={"result": "invalid_signature"})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")

    body = await request.body()
    if not verify_signature(body, x_signature):
        request.state.result = "invalid_signature"
        metrics.inc_webhook_request("invalid_signature")
        logger.error("Invalid signature", extra={"result": "invalid_signature"})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not config.validate():
        logger.error("WEBHOOK_SECRET not set - service not ready")
    else:
        logger.info("Service starting up", extra={"database_url": config.DATABASE_URL})

    if config.ENABLE_ASYNC_WORKER and config.WEBHOOK_FORWARD_URL:
        worker.start()

    yield

    if config.ENABLE_ASYNC_WORKER and config.WEBHOOK_FORWARD_URL:
        await worker.stop()
    db.close()
    logger.info("Service shutting down")


app = FastAPI(
    title="HookPulse Webhook API",
    version="1.0.0",
    description="High-performance, resilient webhook ingestion and forwarding engine with HMAC-SHA256 authentication, idempotency, Prometheus metrics, and async retry worker.",
    lifespan=lifespan
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/webhook":
        request.state.result = "validation_error"
        metrics.inc_webhook_request("validation_error")
    return await request_validation_exception_handler(request, exc)


@app.middleware("http")
async def logging_and_metrics_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    response = await call_next(request)

    latency_ms = (time.perf_counter() - start_time) * 1000

    response.headers["X-Response-Time-Ms"] = f"{latency_ms:.2f}"
    response.headers["Server-Timing"] = f"total;dur={latency_ms:.2f}"

    log_extra = {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "latency_ms": round(latency_ms, 2),
    }

    if hasattr(request.state, "message_id"):
        log_extra["message_id"] = request.state.message_id
    if hasattr(request.state, "dup"):
        log_extra["dup"] = request.state.dup
    if hasattr(request.state, "result"):
        log_extra["result"] = request.state.result

    logger.info("Request processed", extra=log_extra)

    metrics.inc_http_request(request.url.path, response.status_code, method=request.method)
    metrics.observe_latency(latency_ms)

    return response


@app.get("/", tags=["Root"])
async def root():
    return {
        "service": "HookPulse Webhook API",
        "status": "running",
        "version": "1.0.0",
        "docs_url": "/docs"
    }


@app.post("/webhook", dependencies=[Depends(verify_webhook_auth)], tags=["Webhook"])
async def webhook(
    request: Request,
    payload: WebhookMessage = Body(...),
    x_signature: Optional[str] = Header(None, alias="X-Signature")
):
    request.state.message_id = payload.message_id

    should_forward = bool(config.ENABLE_ASYNC_WORKER and config.WEBHOOK_FORWARD_URL)
    delivery_id = str(uuid.uuid4()) if should_forward else None
    target_url = config.WEBHOOK_FORWARD_URL if should_forward else None

    # Offload blocking database I/O to worker thread to prevent event loop starvation under concurrency
    inserted = await asyncio.to_thread(
        db.insert_message_with_delivery,
        payload.message_id,
        payload.from_,
        payload.to,
        payload.ts,
        payload.text,
        delivery_id=delivery_id,
        target_url=target_url,
    )

    if inserted:
        request.state.dup = False
        request.state.result = "created"
        metrics.inc_webhook_request("created")
        if should_forward:
            metrics.inc_worker_job("enqueued")
            logger.info("Enqueued webhook delivery", extra={"delivery_id": delivery_id, "message_id": payload.message_id})
    else:
        request.state.dup = True
        request.state.result = "duplicate"
        metrics.inc_webhook_request("duplicate")

    return {"status": "ok"}


@app.get("/messages", response_model=MessagesListResponse, tags=["Messages"])
async def get_messages(
    limit: int = Query(50, ge=1, le=100, description="Page limit (1-100)"),
    offset: int = Query(0, ge=0, description="Offset index"),
    from_: Optional[str] = Query(None, alias="from", description="Filter by sender MSISDN"),
    since: Optional[str] = Query(None, description="Filter messages on or after this ISO-8601 timestamp"),
    q: Optional[str] = Query(None, description="Case-insensitive text substring query"),
):
    return await asyncio.to_thread(db.get_messages, limit, offset, from_, since, q)


@app.get("/stats", response_model=StatsResponse, tags=["Analytics"])
async def get_stats():
    return await asyncio.to_thread(db.get_stats)


@app.get("/worker/stats", tags=["Worker"])
async def get_worker_stats():
    return worker.get_stats()


@app.post("/worker/deliveries/{delivery_id}/retry", tags=["Worker"])
async def retry_delivery(delivery_id: str):
    success = worker.retry_delivery(delivery_id)
    if not success:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return {"status": "retry_scheduled", "delivery_id": delivery_id}


@app.get("/health/live", tags=["Health"])
async def health_live():
    return {"status": "ok"}


@app.get("/health/ready", tags=["Health"])
async def health_ready():
    if not config.validate():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="WEBHOOK_SECRET not configured")
    is_healthy = await asyncio.to_thread(db.is_healthy)
    if not is_healthy:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not healthy")
    return {"status": "ready"}


@app.get("/metrics", response_class=PlainTextResponse, tags=["Monitoring"])
async def get_metrics(request: Request):
    expected_token = os.getenv("METRICS_TOKEN", "")
    supplied_token = request.headers.get("Authorization", "")

    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Metrics authentication is not configured",
        )

    expected_header = f"Bearer {expected_token}"

    if not secrets.compare_digest(supplied_token, expected_header):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    return metrics.generate_metrics()
