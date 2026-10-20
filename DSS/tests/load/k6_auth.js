/**
 * DSS Load Test — Auth endpoint
 * Тестирует login под нагрузкой (20 VU, 1 min)
 *
 * Запуск: k6 run tests/load/k6_auth.js
 */

import http from 'k6/http'
import { check, sleep } from 'k6'
import { Rate } from 'k6/metrics'

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000'

export const options = {
  vus: 20,
  duration: '60s',
  thresholds: {
    http_req_duration: ['p(95)<1000'],  // login должен быть быстрым
    http_req_failed:   ['rate<0.01'],
  },
}

const errorRate = new Rate('errors')

const USERS = [
  { username: 'admin',      password: 'CHANGE_ME' },
  { username: 'exp_dir',    password: 'Test2026' },
  { username: 'exp_ver',    password: 'Test2026' },
  { username: 'akim_obl',   password: 'Test2026' },
  { username: 'akim_petro', password: 'Test2026' },
]

export default function () {
  const user = USERS[Math.floor(Math.random() * USERS.length)]

  // Login
  const loginRes = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ username: user.username, password: user.password }),
    { headers: { 'Content-Type': 'application/json' } }
  )
  const loginOk = check(loginRes, {
    'login 200':         (r) => r.status === 200,
    'has access_token':  (r) => r.json('access_token') !== undefined,
  })
  errorRate.add(!loginOk)

  if (!loginOk) {
    sleep(1)
    return
  }

  const token = loginRes.json('access_token')

  // GET /me immediately after login
  const meRes = http.get(`${BASE_URL}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  const meOk = check(meRes, {
    'me 200':        (r) => r.status === 200,
    'me has username': (r) => r.json('username') !== undefined,
  })
  errorRate.add(!meOk)

  sleep(2)
}
