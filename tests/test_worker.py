import pytest
import httpx
from app.worker import DeliveryWorker

@pytest.mark.asyncio
async def test_worker_successful_delivery():
    from app.storage import SQLiteStorage
    storage = SQLiteStorage(":memory:")
    
    # Insert message
    storage.insert_message("w_m1", "+919876543210", "+14155550100", "2025-01-15T10:00:00Z", "Worker test")
    
    worker = DeliveryWorker(storage, forward_url="http://mock.test/endpoint")
    delivery_id = worker.enqueue_for_forwarding("w_m1")
    assert delivery_id is not None

    stats_before = worker.get_stats()
    assert stats_before["pending"] == 1

    # Mock successful HTTP client
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"received": True}))
    async with httpx.AsyncClient(transport=transport) as client:
        deliveries = storage.fetch_pending_deliveries()
        assert len(deliveries) == 1
        await worker.process_delivery(deliveries[0], client)

    stats_after = worker.get_stats()
    assert stats_after["delivered"] == 1
    assert stats_after["pending"] == 0

@pytest.mark.asyncio
async def test_worker_retry_and_dlq():
    from app.storage import SQLiteStorage
    storage = SQLiteStorage(":memory:")
    
    storage.insert_message("w_m2", "+919876543210", "+14155550100", "2025-01-15T10:00:00Z", "Failing endpoint")
    
    worker = DeliveryWorker(storage, forward_url="http://mock.test/fail")
    delivery_id = worker.enqueue_for_forwarding("w_m2")
    assert delivery_id is not None

    # Mock 500 error transport
    transport = httpx.MockTransport(lambda req: httpx.Response(500, text="Internal Server Error"))
    async with httpx.AsyncClient(transport=transport) as client:
        # First attempt -> retry status
        deliveries = storage.fetch_pending_deliveries()
        await worker.process_delivery(deliveries[0], client)
        
        stats = worker.get_stats()
        assert stats["retrying"] == 1

        # Second attempt (simulate reaching retry attempt)
        deliveries[0]["attempts"] = 1
        await worker.process_delivery(deliveries[0], client)
        
        # Third attempt (hits max retries 3 -> fails to DLQ)
        deliveries[0]["attempts"] = 2
        await worker.process_delivery(deliveries[0], client)

    stats = worker.get_stats()
    assert stats["failed"] == 1

    # Test manual retry
    retried = worker.retry_delivery(delivery_id)
    assert retried is True
    stats = worker.get_stats()
    assert stats["pending"] == 1



@pytest.mark.asyncio
async def test_manual_retry_succeeds_after_previous_failure():
    from app.storage import SQLiteStorage

    storage = SQLiteStorage(":memory:")
    storage.insert_message(
        "w_manual_retry",
        "+919876543210",
        "+14155550100",
        "2025-01-15T10:00:00Z",
        "Manual retry test",
    )

    worker = DeliveryWorker(
        storage,
        forward_url="http://mock.test/endpoint",
    )
    delivery_id = worker.enqueue_for_forwarding("w_manual_retry")
    assert delivery_id is not None

    # Simulate the delivery reaching its failed state.
    storage.update_delivery(
        delivery_id,
        status="failed",
        attempts=3,
        last_error="Simulated receiver failure",
        next_retry_at=None,
    )
    assert worker.get_stats()["failed"] == 1

    # Manually schedule the failed delivery again.
    assert worker.retry_delivery(delivery_id) is True
    assert worker.get_stats()["pending"] == 1

    # Mock a successful receiver response.
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"received": True})
    )

    async with httpx.AsyncClient(transport=transport) as client:
        deliveries = storage.fetch_pending_deliveries()
        assert len(deliveries) == 1

        await worker.process_delivery(deliveries[0], client)

    stats = worker.get_stats()
    assert stats["delivered"] == 1
    assert stats["failed"] == 0
    assert stats["pending"] == 0