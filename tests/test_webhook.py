import json
from tests.conftest import compute_signature

from concurrent.futures import ThreadPoolExecutor
import app.main as app_main

def test_webhook_invalid_signature(client):
    body = json.dumps({
        "message_id": "m1",
        "from": "+919876543210",
        "to": "+14155550100",
        "ts": "2025-01-15T10:00:00Z",
        "text": "Hello"
    })

    response = client.post(
        "/webhook",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": "invalid_signature"
        }
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid signature"

def test_webhook_valid_insert(client):
    body = json.dumps({
        "message_id": "m1",
        "from": "+919876543210",
        "to": "+14155550100",
        "ts": "2025-01-15T10:00:00Z",
        "text": "Hello"
    })

    signature = compute_signature(body)

    response = client.post(
        "/webhook",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": signature
        }
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_webhook_duplicate(client):
    body = json.dumps({
        "message_id": "m2",
        "from": "+919876543210",
        "to": "+14155550100",
        "ts": "2025-01-15T10:00:00Z",
        "text": "Hello"
    })

    signature = compute_signature(body)
    headers = {
        "Content-Type": "application/json",
        "X-Signature": signature
    }

    response1 = client.post("/webhook", data=body, headers=headers)
    assert response1.status_code == 200

    response2 = client.post("/webhook", data=body, headers=headers)
    assert response2.status_code == 200
    assert response2.json() == {"status": "ok"}

def test_webhook_validation_error(client):
    body = json.dumps({
        "message_id": "",
        "from": "invalid",
        "to": "+14155550100",
        "ts": "2025-01-15T10:00:00Z",
        "text": "Hello"
    })

    signature = compute_signature(body)

    response = client.post(
        "/webhook",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": signature
        }
    )

    assert response.status_code == 422

def test_webhook_missing_signature(client):
    body = json.dumps({
        "message_id": "m3",
        "from": "+919876543210",
        "to": "+14155550100",
        "ts": "2025-01-15T10:00:00Z",
        "text": "Hello"
    })

    response = client.post(
        "/webhook",
        data=body,
        headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 401

def test_concurrent_duplicate_webhooks_create_one_message(client):
    message_id = "concurrent-idempotency-001"

    body = json.dumps({
        "message_id": message_id,
        "from": "+919876543210",
        "to": "+14155550100",
        "ts": "2025-01-15T10:00:00Z",
        "text": "Concurrent test"
    })

    headers = {
        "Content-Type": "application/json",
        "X-Signature": compute_signature(body)
    }

    def send_request(_):
        response = client.post(
            "/webhook",
            content=body,
            headers=headers
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=5) as executor:
        statuses = list(executor.map(send_request, range(5)))

    assert statuses == [200] * 5

    result = app_main.db.get_messages(limit=100)

    matching_messages = [
        message for message in result["data"]
        if message["message_id"] == message_id
    ]

    assert len(matching_messages) == 1
