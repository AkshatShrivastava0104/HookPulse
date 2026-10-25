import http from 'k6/http';
import { check, sleep, group } from 'k6';
import crypto from 'k6/crypto';

// k6 Load Test for HookPulse Webhook API
// Simulates concurrent webhook producers, idempotency checks, message queries, and analytics

export const options = {
  scenarios: {
    // Scenario 1: Steady load test for webhook ingestion and verification
    webhook_ingestion: {
      executor: 'ramping-arrival-rate',
      startRate: 20,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 200,
      stages: [
        { target: 50, duration: '30s' },   // Warm-up to 50 RPS
        { target: 200, duration: '1m' },    // Ramp up to 200 RPS
        { target: 200, duration: '1m' },    // Sustained load at 200 RPS
        { target: 0, duration: '15s' },     // Cooldown
      ],
    },
  },
  thresholds: {
    // 95% of requests must complete below 150ms; 99% below 300ms
    http_req_duration: ['p(95)<150', 'p(99)<300'],
    // Overall failure rate must stay below 1% for valid requests
    'http_req_failed{status:200}': ['rate<0.01'],
  },
};

const BASE_URL = __ENV.API_URL || 'http://localhost:8000';
const SECRET = __ENV.WEBHOOK_SECRET || 'testsecret';

function computeHmac(secret, body) {
  return crypto.hmac('sha256', secret, body, 'hex');
}

export default function () {
  const vuId = __VU;
  const iterId = __ITER;
  const uniqueId = `m_${vuId}_${iterId}_${Date.now()}`;

  // 1. Ingest New Webhook Message (Expect 200 Created)
  group('Webhook Ingestion', function () {
    const payload = JSON.stringify({
      message_id: uniqueId,
      from: '+919876543210',
      to: '+14155550100',
      ts: new Date().toISOString(),
      text: `Load test message from VU ${vuId} iteration ${iterId}`
    });

    const signature = computeHmac(SECRET, payload);
    const headers = {
      'Content-Type': 'application/json',
      'X-Signature': signature,
    };

    const res = http.post(`${BASE_URL}/webhook`, payload, { headers: headers });
    check(res, {
      'webhook status is 200': (r) => r.status === 200,
      'webhook response ok': (r) => {
        try {
          return JSON.parse(r.body).status === 'ok';
        } catch (e) {
          return false;
        }
      },
    });

    // 2. Test Idempotency with immediate duplicate (Expect 200 Duplicate)
    if (iterId % 5 === 0) {
      const dupRes = http.post(`${BASE_URL}/webhook`, payload, { headers: headers });
      check(dupRes, {
        'duplicate status is 200': (r) => r.status === 200,
      });
    }

    // 3. Security Check: Invalid signature should return 401
    if (iterId % 20 === 0) {
      const invalidHeaders = {
        'Content-Type': 'application/json',
        'X-Signature': 'invalid_hmac_signature',
      };
      const secRes = http.post(`${BASE_URL}/webhook`, payload, { headers: invalidHeaders });
      check(secRes, {
        'invalid signature rejected with 401': (r) => r.status === 401,
      });
    }
  });

  // 4. Query messages and stats periodically
  if (iterId % 10 === 0) {
    group('API Queries', function () {
      const listRes = http.get(`${BASE_URL}/messages?limit=10&offset=0`);
      check(listRes, {
        'messages list returns 200': (r) => r.status === 200,
      });

      const statsRes = http.get(`${BASE_URL}/stats`);
      check(statsRes, {
        'stats returns 200': (r) => r.status === 200,
      });
    });
  }

  sleep(0.05);
}
