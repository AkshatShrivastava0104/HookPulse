import http from 'k6/http';
import { check, sleep } from 'k6';
import crypto from 'k6/crypto';

export const options = {
  vus: 5,
  duration: '10s',
  thresholds: {
    http_req_duration: ['p(95)<100'],
  },
};

const BASE_URL = __ENV.API_URL || 'http://localhost:8000';
const SECRET = __ENV.WEBHOOK_SECRET || 'testsecret';

export default function () {
  const payload = JSON.stringify({
    message_id: `smoke_${__VU}_${__ITER}_${Date.now()}`,
    from: '+919876543210',
    to: '+14155550100',
    ts: new Date().toISOString(),
    text: 'Smoke test payload'
  });

  const signature = crypto.hmac('sha256', SECRET, payload, 'hex');

  const res = http.post(`${BASE_URL}/webhook`, payload, {
    headers: {
      'Content-Type': 'application/json',
      'X-Signature': signature,
    },
  });

  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  sleep(0.1);
}
