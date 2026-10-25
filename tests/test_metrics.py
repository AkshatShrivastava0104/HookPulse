import json
from tests.conftest import compute_signature

def test_metrics_endpoint_format(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    content = response.text
    assert "# HELP http_requests_total" in content
    assert "# TYPE http_requests_total counter"
    assert "# HELP webhook_requests_total" in content
    assert "# TYPE webhook_requests_total counter"
    assert "# HELP request_latency_ms_bucket" in content
    assert "# TYPE request_latency_ms_bucket histogram"

def test_metrics_increment_on_requests(client):
    body = json.dumps({
        "message_id": "metrics_m1",
        "from": "+919876543210",
        "to": "+14155550100",
        "ts": "2025-01-15T10:00:00Z",
        "text": "Testing metrics"
    })
    sig = compute_signature(body)

    # Valid webhook request
    r = client.post("/webhook", data=body, headers={"Content-Type": "application/json", "X-Signature": sig})
    assert r.status_code == 200

    # Invalid webhook request
    client.post("/webhook", data=body, headers={"Content-Type": "application/json", "X-Signature": "invalid"})

    # Check metrics
    metrics_res = client.get("/metrics")
    assert metrics_res.status_code == 200
    content = metrics_res.text
    assert 'webhook_requests_total{result="created"}' in content
    assert 'webhook_requests_total{result="invalid_signature"}' in content
    assert 'http_requests_total{path="/webhook",status="200"}' in content
    assert 'http_requests_total{path="/webhook",status="401"}' in content
