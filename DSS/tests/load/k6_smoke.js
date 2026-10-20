/**
 * DSS Load Test — Post-deploy SMOKE (лёгкий, для CI)
 * Цель НФТ: p95 < 3000 ms, error rate < 1% под небольшой конкуренцией.
 *
 * Запускается в CI после деплоя (job `smoke` в .gitea/workflows/deploy.yml)
 * через docker-образ grafana/k6 внутри сети dss-net:
 *   BASE_URL=http://dss-backend:8000  AI_URL=http://dss-ai-service:8001
 *
 * Режимы:
 *   - без ADMIN_PASSWORD: health + доступность (без авторизации);
 *   - с ADMIN_PASSWORD (Gitea secret SMOKE_ADMIN_PASSWORD): + авторизованные
 *     read-эндпоинты (реальная латентность бэкенда под нагрузкой).
 *
 * Локальный прогон:
 *   docker run --rm --network dss-net -e BASE_URL=http://dss-backend:8000 \
 *     -v $PWD/tests/load:/scripts grafana/k6 run /scripts/k6_smoke.js
 */

import http from 'k6/http'
import { check } from 'k6'
import { Rate, Trend } from 'k6/metrics'

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000'
const AI_URL = __ENV.AI_URL || ''
const ADMIN_USER = __ENV.ADMIN_USER || 'admin'
const ADMIN_PASSWORD = __ENV.ADMIN_PASSWORD || ''

export const options = {
  stages: [
    { duration: '10s', target: 8 }, // ramp up
    { duration: '20s', target: 8 }, // steady light load
    { duration: '10s', target: 0 }, // ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<3000'], // НФТ: < 3 сек
    http_req_failed: ['rate<0.01'], // < 1% ошибок
    errors: ['rate<0.01'],
  },
}

const errorRate = new Rate('errors')
const readDuration = new Trend('read_duration')

export function setup() {
  if (!ADMIN_PASSWORD) return { token: null }
  const res = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ username: ADMIN_USER, password: ADMIN_PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } },
  )
  const ok = check(res, { 'login ok': (r) => r.status === 200 })
  return { token: ok ? res.json('access_token') : null }
}

export default function (data) {
  // ── Доступность бэкенда (без авторизации) ──
  {
    const res = http.get(`${BASE_URL}/health`)
    errorRate.add(!check(res, { 'backend /health 200': (r) => r.status === 200 }))
    readDuration.add(res.timings.duration)
  }

  // ── Доступность AI-сервиса ──
  if (AI_URL) {
    const res = http.get(`${AI_URL}/health`)
    errorRate.add(!check(res, { 'ai /health 200': (r) => r.status === 200 }))
  }

  // ── Авторизованные read-эндпоинты (если задан пароль) ──
  if (data.token) {
    const headers = { Authorization: `Bearer ${data.token}` }
    {
      const res = http.get(`${BASE_URL}/api/v1/regions`, { headers })
      errorRate.add(!check(res, { 'regions 200': (r) => r.status === 200 }))
      readDuration.add(res.timings.duration)
    }
    {
      const res = http.get(`${BASE_URL}/api/v1/indicators?page_size=50`, { headers })
      errorRate.add(!check(res, { 'indicators 200': (r) => r.status === 200 }))
      readDuration.add(res.timings.duration)
    }
  }
}
