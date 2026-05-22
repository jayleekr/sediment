// 100 sessions reconnect within 5 seconds (VS Code reload simulation).
// Each "session" does: device_start → poll once → search once → done.
// Expectation: no 5xx, nginx accept queue absorbs the burst.

import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE_URL || 'http://localhost:10101';
const JWT  = __ENV.JWT;
if (!JWT) throw new Error('JWT env var required');

export const options = {
  scenarios: {
    storm: {
      executor: 'per-vu-iterations',
      vus: 100,
      iterations: 1,
      startTime: '0s',
      maxDuration: '30s',
      gracefulStop: '15s',
    },
  },
  thresholds: {
    'http_req_failed': ['rate<0.02'],
    'http_req_duration{name:device_start}': ['p(95)<2000'],
  },
};

export default function () {
  const h = { Authorization: `Bearer ${JWT}` };

  // 1. /oauth-device/start — this is the request a fresh login makes
  let r = http.post(`${BASE}/api/v1/auth/oauth-device/start`,
    JSON.stringify({client_id: 'sediment-cli'}),
    { headers: { 'Content-Type': 'application/json' }, tags: { name: 'device_start' } }
  );
  check(r, { 'device_start ok': (resp) => resp.status === 200 });

  // 2. whoami via existing JWT (sessions already logged in)
  r = http.get(`${BASE}/api/v1/auth/whoami`, { headers: h, tags: { name: 'whoami' } });
  check(r, { 'whoami ok': (resp) => resp.status === 200 });

  // 3. a quick search
  r = http.get(`${BASE}/api/v1/library/search?q=research&limit=5`,
    { headers: h, tags: { name: 'search' } });
  check(r, { 'search no 5xx': (resp) => resp.status < 500 });
}
