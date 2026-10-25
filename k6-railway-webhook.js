import http from 'k6/http';
import crypto from 'k6/crypto';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 1 },
    { duration: '30s', target: 3 },
    { duration: '30s', target: 5 },
    { duration: '15s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
  },
};

export default function () {
  const baseURL = __ENV.BASE_URL;
  const secret = __ENV.WEBHOOK_SECRET;

  if (!baseURL || !secret) {
    throw new Error('BASE_URL and WEBHOOK_SECRET are required');
  }

  const payload = JSON.stringify({
    message_id: `k6-prod-${__VU}-${__ITER}-${Date.now()}`,
    from: '+919876543210',
    to: '+919876543211',
    ts: new Date().toISOString(),
    message: 'HookPulse production load test',
  });

  const signature = crypto.hmac('sha256', secret, payload, 'hex');

  const response = http.post(`${baseURL}/webhook`, payload, {
    headers: {
      'Content-Type': 'application/json',
      'X-Signature': signature,
    },
    tags: { endpoint: 'webhook' },
  });

  check(response, {
    'webhook returns 200': (r) => r.status === 200,
  });

  sleep(1);
}
