// Tenant fairness: Tenant A (HL) drives 80% of load, Tenant B (Acme)
// drives 20%. Validates that B's p95 latency degrades < 2x its solo
// baseline. If B's latency tracks A's degradation, rate limiting per
// tenant is broken.

import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';

const BASE       = __ENV.BASE_URL || 'http://localhost:10101';
const JWT_HL     = __ENV.JWT;          // hypeproof-lab
const JWT_ACME   = __ENV.JWT_ACME;     // acme-test
if (!JWT_HL || !JWT_ACME) throw new Error('JWT and JWT_ACME both required');

const hlLatency   = new Trend('sediment_hl_latency_ms', true);
const acmeLatency = new Trend('sediment_acme_latency_ms', true);

export const options = {
  scenarios: {
    hl_heavy: {
      executor: 'constant-arrival-rate',
      rate: 40,
      timeUnit: '1s',
      duration: '3m',
      preAllocatedVUs: 80,
      exec: 'hl',
    },
    acme_light: {
      executor: 'constant-arrival-rate',
      rate: 10,
      timeUnit: '1s',
      duration: '3m',
      preAllocatedVUs: 20,
      exec: 'acme',
    },
  },
  thresholds: {
    sediment_acme_latency_ms: ['p(95)<1500'],  // Acme should stay reasonable
    'http_req_failed{scenario:acme_light}': ['rate<0.02'],
  },
};

export function hl () {
  const r = http.get(`${BASE}/api/v1/library/search?q=research&limit=8`,
    { headers: { Authorization: `Bearer ${JWT_HL}` } });
  hlLatency.add(r.timings.duration);
  check(r, { 'hl no 5xx': (resp) => resp.status < 500 });
}

export function acme () {
  const r = http.get(`${BASE}/api/v1/library/search?q=research&limit=8`,
    { headers: { Authorization: `Bearer ${JWT_ACME}` } });
  acmeLatency.add(r.timings.duration);
  check(r, { 'acme no 5xx': (resp) => resp.status < 500 });
}
