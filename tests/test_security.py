import json
import hmac
import hashlib
from tests.conftest import compute_signature

def test_security_backdoor_rejected(client):
    """
    Ensure the previous vulnerability ('demo-signature') is completely eliminated
    and rejected with 401.
    """
    body = json.dumps({
        "message_id": "sec_m1",
        "from": "+919876543210",
        "to": "+14155550100",
        "ts": "2025-01-15T10:00:00Z",
        "text": "Attempting bypass"
    })

    response = client.post(
        "/webhook",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": "demo-signature"
        }
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid signature"

def test_security_tampered_payload(client):
    """
    Ensure that tampering even a single character in the body invalidates the HMAC.
    """
    body = json.dumps({
        "message_id": "sec_m2",
        "from": "+919876543210",
        "to": "+14155550100",
        "ts": "2025-01-15T10:00:00Z",
        "text": "Original text"
    })
    sig = compute_signature(body)

    # Tamper body
    tampered_body = body.replace("Original text", "Tampered text")

    response = client.post(
        "/webhook",
        data=tampered_body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": sig
        }
    )
    assert response.status_code == 401

def test_security_wrong_secret(client):
    """
    Sign with a wrong secret and verify rejection.
    """
    body = json.dumps({
        "message_id": "sec_m3",
        "from": "+919876543210",
        "to": "+14155550100",
        "ts": "2025-01-15T10:00:00Z",
        "text": "Hello"
    })
    wrong_sig = compute_signature(body, secret="wrong_secret_key")

    response = client.post(
        "/webhook",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": wrong_sig
        }
    )
    assert response.status_code == 401

def test_security_empty_signature(client):
    """
    Empty signature header should be rejected with 401.
    """
    body = json.dumps({
        "message_id": "sec_m4",
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
            "X-Signature": ""
        }
    )
    assert response.status_code == 401
