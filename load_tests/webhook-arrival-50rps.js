import http from 'k6/http';
import { check } from 'k6';
import crypto from 'k6/crypto';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const SECRET = __ENV.WEBHOOK_SECRET;

if (!SECRET) {
  throw new Error('Set WEBHOOK_SECRET before running this test');
}

export const options = {
  scenarios: {
    webhook_rate: {
      executor: 'constant-arrival-rate',
      rate: 50,
      timeUnit: '1s',
      duration: '30s',
      preAllocatedVUs: 60,
      maxVUs: 100,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<500'],
    checks: ['rate>0.99'],
    dropped_iterations: ['count==0'],
  },
};

export default function () {
  const payload = JSON.stringify({
    message_id: `k6-50rps-${__VU}-${__ITER}-${Date.now()}`,
    from: '+919876543210',
    to: '+14155550100',
    ts: new Date().toISOString(),
    text: 'HookPulse fixed 50 RPS test',
  });

  const signature = crypto.hmac('sha256', SECRET, payload, 'hex');

  const response = http.post(`${BASE_URL}/webhook`, payload, {
    headers: {
      'Content-Type': 'application/json',
      'X-Signature': signature,
    },
    timeout: '10s',
  });

  check(response, {
    'webhook accepted': (r) => r.status === 200,
  });
}
