import http from 'k6/http';
import { check } from 'k6';
import crypto from 'k6/crypto';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const SECRET = __ENV.WEBHOOK_SECRET;

if (!SECRET) {
  throw new Error('WEBHOOK_SECRET is missing');
}

export const options = {
  vus: 1,
  iterations: 1,
};

export default function () {
  const payload = JSON.stringify({
    message_id: 'idempotency-k6-001',
    from: '+15550001001',
    to: '+14155550100',
    ts: '2026-10-25T12:00:00Z',
    text: 'HookPulse idempotency test',
  });

  const signature = crypto.hmac('sha256', SECRET, payload, 'hex');

  const params = {
    headers: {
      'Content-Type': 'application/json',
      'X-Signature': signature,
    },
  };

  for (let i = 1; i <= 2; i++) {
    const response = http.post(
      `${BASE_URL}/webhook`,
      payload,
      params
    );

    console.log(`Request ${i}: HTTP ${response.status}`);

    check(response, {
      'webhook accepted': (r) => r.status === 200,
    });
  }
}
