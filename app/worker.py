import asyncio
import hashlib
import hmac
import json
import logging
import random
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import httpx

from app.config import config
from app.metrics import metrics

logger = logging.getLogger("webhook_api.worker")


class DeliveryWorker:
    def __init__(self, db, forward_url: Optional[str] = None):
        self.db = db
        self.forward_url = forward_url or config.WEBHOOK_FORWARD_URL
        self.is_running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not self.is_running:
            self.is_running = True
            self._task = asyncio.create_task(self.run_loop())
            logger.info("Delivery worker started", extra={"forward_url": self.forward_url})

    async def stop(self):
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("Delivery worker stopped")

    def enqueue_for_forwarding(self, message_id: str, forward_url: Optional[str] = None) -> Optional[str]:
        target = forward_url or self.forward_url
        if not target:
            return None
        delivery_id = str(uuid.uuid4())
        inserted = self.db.enqueue_delivery(delivery_id, message_id, target)
        if inserted:
            metrics.inc_worker_job("enqueued")
            logger.info("Enqueued webhook delivery", extra={"delivery_id": delivery_id, "message_id": message_id})
            return delivery_id
        return None

    async def process_delivery(self, delivery: Dict[str, Any], client: httpx.AsyncClient):
        delivery_id = delivery["id"]
        message_id = delivery["message_id"]
        target_url = delivery["target_url"]
        attempts = delivery["attempts"] + 1

        payload_dict = {
            "message_id": message_id,
            "from": delivery.get("from_msisdn"),
            "to": delivery.get("to_msisdn"),
            "ts": delivery.get("ts"),
            "text": delivery.get("text")
        }
        body_bytes = json.dumps(payload_dict).encode("utf-8")

        headers = {
            "Content-Type": "application/json"
        }
        if config.WEBHOOK_SECRET:
            sig = hmac.new(config.WEBHOOK_SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()
            headers["X-Signature"] = sig

        try:
            response = await client.post(target_url, content=body_bytes, headers=headers, timeout=5.0)
            if response.status_code in (200, 201, 202, 204):
                self.db.update_delivery(delivery_id, status="delivered", attempts=attempts)
                metrics.inc_worker_job("delivered")
                logger.info("Webhook delivery succeeded", extra={
                    "delivery_id": delivery_id,
                    "status_code": response.status_code,
                    "attempts": attempts
                })
                return
            else:
                error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {str(exc)}"

        # If failed, check retries
        if attempts >= config.WORKER_MAX_RETRIES:
            self.db.update_delivery(delivery_id, status="failed", attempts=attempts, last_error=error_msg)
            metrics.inc_worker_job("failed")
            logger.error(f"Webhook delivery exhausted retries (moved to DLQ): {error_msg}", extra={
                "delivery_id": delivery_id,
                "attempts": attempts,
                "error": error_msg
            })
        else:
            delay_seconds = (config.WORKER_BACKOFF_FACTOR ** attempts) + random.uniform(0.1, 0.5)
            next_retry_dt = datetime.utcnow() + timedelta(seconds=delay_seconds)
            next_retry_at = next_retry_dt.isoformat() + 'Z'
            self.db.update_delivery(delivery_id, status="retry", attempts=attempts,
                                   last_error=error_msg, next_retry_at=next_retry_at)
            metrics.inc_worker_job("retry")
            logger.warning(f"Webhook delivery scheduled for retry: {error_msg}", extra={
                "delivery_id": delivery_id,
                "attempts": attempts,
                "next_retry_at": next_retry_at,
                "error": error_msg
            })

    async def run_loop(self):
        async with httpx.AsyncClient() as client:
            while self.is_running:
                try:
                    pending = self.db.fetch_pending_deliveries(limit=10)
                    if pending:
                        for item in pending:
                            if not self.is_running:
                                break
                            await self.process_delivery(item, client)
                    else:
                        await asyncio.sleep(config.WORKER_POLL_INTERVAL)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Error in worker loop", exc_info=e)
                    await asyncio.sleep(config.WORKER_POLL_INTERVAL)

    def retry_delivery(self, delivery_id: str) -> bool:
        now = datetime.utcnow().isoformat() + 'Z'
        return self.db.update_delivery(delivery_id, status="pending", attempts=0, last_error=None, next_retry_at=now)

    def get_stats(self) -> Dict[str, Any]:
        return self.db.get_delivery_stats()

