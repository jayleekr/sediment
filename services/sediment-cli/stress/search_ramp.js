// QPS ramp: 1 → 50 QPS over 5 minutes.
// Finds the search QPS knee (where p95 latency starts to balloon).

import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://localhost:10101';
const JWT  = __ENV.JWT;
if (!JWT) throw new Error('JWT env var required');

const searchLatency = new Trend('sediment_search_latency_ms', true);

export const options = {
  scenarios: {
    ramp: {
      executor: 'ramping-arrival-rate',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 200,
      stages: [
        { target: 1,  duration: '30s' },   // warm-up
        { target: 50, duration: '5m' },    // ramp
        { target: 50, duration: '2m' },    // hold
      ],
    },
  },
  // No hard thresholds — this is a discovery run.
};

export default function () {
  const r = http.get(
    `${BASE}/api/v1/library/search?q=research&limit=8`,
    { headers: { Authorization: `Bearer ${JWT}` } }
  );
  searchLatency.add(r.timings.duration);
  check(r, { 'no 5xx': (resp) => resp.status < 500 });
}
