import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: 1,
  duration: '30s',
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1000'],
  },
};

export default function () {
  const res = http.get(`${__ENV.BASE_URL}/health/live`);

  check(res, {
    'status is 200': (r) => r.status === 200,
    'health status is ok': (r) => r.json('status') === 'ok',
  });

  sleep(1);
}
