import http from 'k6/http';
import crypto from 'k6/crypto';
import { check } from 'k6';

export const options = {
  scenarios: {
    webhook_fixed_rate: {
      executor: 'constant-arrival-rate',
      rate: 5,
      timeUnit: '1s',
      duration: '60s',
      preAllocatedVUs: 10,
      maxVUs: 20,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
    dropped_iterations: ['count==0'],
  },
};

export default function () {
  const baseURL = __ENV.BASE_URL;
  const secret = __ENV.WEBHOOK_SECRET;

  if (!baseURL || !secret) {
    throw new Error('BASE_URL and WEBHOOK_SECRET are required');
  }

  const payload = JSON.stringify({
    message_id: `k6-fixed-${__VU}-${__ITER}-${Date.now()}`,
    from: '+919876543210',
    to: '+919876543211',
    ts: new Date().toISOString(),
    message: 'HookPulse fixed-rate production test',
  });

  const signature = crypto.hmac('sha256', secret, payload, 'hex');

  const response = http.post(`${baseURL}/webhook`, payload, {
    headers: {
      'Content-Type': 'application/json',
      'X-Signature': signature,
    },
    tags: { endpoint: 'webhook', test: 'fixed_rate_5rps' },
  });

  check(response, {
    'webhook returns 200': (r) => r.status === 200,
  });
}
