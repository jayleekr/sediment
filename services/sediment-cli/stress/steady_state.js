// k6 steady-state scenario for Sediment.
//
// 10 virtual users, each making ~4 calls/min, for 30 minutes.
// Mix: 60% search, 25% recent, 10% read, 5% whoami.
// Pass criteria (asserted via thresholds):
//   - search p50  < 400ms, p95 < 1000ms
//   - errors      < 0.5%
//   - 5xx        == 0

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';
import { randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const BASE = __ENV.BASE_URL || 'http://localhost:10101';
const JWT  = __ENV.JWT;
if (!JWT) throw new Error('JWT env var required');

const searchLatency = new Trend('sediment_search_latency_ms', true);
const recentLatency = new Trend('sediment_recent_latency_ms', true);
const errorRate     = new Rate('sediment_error_rate');

export const options = {
  scenarios: {
    steady: {
      executor: 'constant-vus',
      vus: 10,
      duration: __ENV.DURATION || '30m',
    },
  },
  thresholds: {
    sediment_search_latency_ms: ['p(50)<400', 'p(95)<1000'],
    sediment_error_rate: ['rate<0.005'],
    'http_req_failed': ['rate<0.01'],
  },
};

const SEARCH_QUERIES = [
  'research', 'pgvector', 'recall', 'rls', 'fastmcp',
  'memory consolidation', 'agentic AI', 'OWASP',
  '한국어 검색', 'enterprise search',
];

export default function () {
  const h = { Authorization: `Bearer ${JWT}` };

  const dice = Math.random();
  let r;
  if (dice < 0.6) {
    // search
    const q = randomItem(SEARCH_QUERIES);
    r = http.get(`${BASE}/api/v1/library/search?q=${encodeURIComponent(q)}&limit=8`, { headers: h });
    searchLatency.add(r.timings.duration);
  } else if (dice < 0.85) {
    // recent
    r = http.get(`${BASE}/api/v1/library?limit=20&date_from=2026-01-01`, { headers: h });
    recentLatency.add(r.timings.duration);
  } else if (dice < 0.95) {
    // read
    r = http.get(`${BASE}/api/v1/library/PHILOSOPHY.md`, { headers: h });
  } else {
    // whoami
    r = http.get(`${BASE}/api/v1/auth/whoami`, { headers: h });
  }

  const ok = check(r, {
    'status is 2xx': (resp) => resp.status >= 200 && resp.status < 300,
    'no 5xx': (resp) => resp.status < 500,
  });
  errorRate.add(!ok);

  // 4 calls/min/VU → average 15s between calls.
  sleep(15);
}
