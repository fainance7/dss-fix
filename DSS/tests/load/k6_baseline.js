/**
 * DSS Load Test — Baseline (50 VU, 2 min)
 * Цель: p95 < 3000 ms, error rate < 1%
 *
 * Запуск:
 *   k6 run tests/load/k6_baseline.js
 *   k6 run --vus 100 --duration 5m tests/load/k6_baseline.js
 */

import http from 'k6/http'
import { check, sleep } from 'k6'
import { Rate, Trend } from 'k6/metrics'

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000'

export const options = {
  stages: [
    { duration: '30s', target: 10 },  // ramp up
    { duration: '60s', target: 50 },  // steady load
    { duration: '30s', target: 0 },   // ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<3000'],  // НФТ: < 3 сек
    http_req_failed:   ['rate<0.01'],   // < 1% ошибок
  },
}

const errorRate = new Rate('errors')
const loginDuration = new Trend('login_duration')
const readDuration  = new Trend('read_duration')

// Shared token (login once per VU)
let token = null

export function setup() {
  const res = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ username: 'admin', password: 'CHANGE_ME' }),
    { headers: { 'Content-Type': 'application/json' } }
  )
  check(res, { 'login ok': (r) => r.status === 200 })
  return { token: res.json('access_token') }
}

export default function (data) {
  const headers = {
    'Authorization': `Bearer ${data.token}`,
    'Content-Type':  'application/json',
  }

  // ── GET /health ──────────────────────────────────────────────
  {
    const res = http.get(`${BASE_URL}/health`)
    errorRate.add(!check(res, { 'health 200': (r) => r.status === 200 }))
  }

  // ── GET /api/v1/regions ──────────────────────────────────────
  {
    const res = http.get(`${BASE_URL}/api/v1/regions`, { headers })
    const ok = check(res, {
      'regions 200':    (r) => r.status === 200,
      'regions array':  (r) => Array.isArray(r.json()),
    })
    readDuration.add(res.timings.duration)
    errorRate.add(!ok)
  }

  // ── GET /api/v1/data-layers ──────────────────────────────────
  {
    const res = http.get(`${BASE_URL}/api/v1/data-layers?page_size=20`, { headers })
    const ok = check(res, { 'data-layers 200': (r) => r.status === 200 })
    readDuration.add(res.timings.duration)
    errorRate.add(!ok)
  }

  // ── GET /api/v1/spatial-data ─────────────────────────────────
  {
    const res = http.get(`${BASE_URL}/api/v1/spatial-data?validation_status=approved&page_size=50`, { headers })
    const ok = check(res, { 'spatial-data 200': (r) => r.status === 200 })
    readDuration.add(res.timings.duration)
    errorRate.add(!ok)
  }

  // ── GET /api/v1/monitoring/sdh ───────────────────────────────
  {
    const res = http.get(`${BASE_URL}/api/v1/monitoring/sdh`, { headers })
    const ok = check(res, {
      'monitoring 200':             (r) => r.status === 200,
      'monitoring has total_layers': (r) => r.json('total_layers') !== undefined,
    })
    readDuration.add(res.timings.duration)
    errorRate.add(!ok)
  }

  sleep(1)
}
