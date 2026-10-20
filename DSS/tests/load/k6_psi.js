/**
 * DSS — нагрузочное испытание по ПМИ (Этап 6), проверка НФТ «Производительность».
 *
 * Профиль: смешанные операции чтения пяти ролей ТЗ (дашборды, показатели, сценарии,
 * задачи, каталог, мониторинг), до 50 одновременных пользователей, пауза 1 с между
 * действиями. Данные не изменяются: вход выполняется один раз на роль в setup().
 *
 * Запуск (учётные данные — через переменные окружения):
 *   k6 run -e BASE_URL=https://dssapi.dwh.kz \
 *     -e U_ADMIN=… -e P_ADMIN=… -e U_EXP=… -e P_EXP=… -e U_VER=… -e P_VER=… \
 *     -e U_OBL=… -e P_OBL=… -e U_RAY=… -e P_RAY=… tests/load/k6_psi.js
 */
import http from 'k6/http'
import { check, sleep } from 'k6'

const BASE = (__ENV.BASE_URL || 'http://localhost:8000') + '/api/v1'

export const options = {
  stages: [
    { duration: '30s', target: 10 },
    { duration: '30s', target: 30 },
    { duration: '2m', target: 50 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<3000'], // НФТ: отклик на стандартные операции < 3 с
    http_req_failed: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
}

const ROLES = [
  ['admin', 'U_ADMIN', 'P_ADMIN'],
  ['exp_dir', 'U_EXP', 'P_EXP'],
  ['exp_ver', 'U_VER', 'P_VER'],
  ['akim_obl', 'U_OBL', 'P_OBL'],
  ['akim_ray', 'U_RAY', 'P_RAY'],
]

const COMMON = ['/regions', '/indicators', '/directions', '/meteo-stations']
const BY_ROLE = {
  admin: ['/users', '/monitoring/system', '/monitoring/sdh', '/audit-log', '/ai/api/v1/models'],
  exp_dir: ['/indicator-values?page_size=100', '/data-layers', '/ai/api/v1/scenarios', '/gis/datasets'],
  exp_ver: ['/indicator-values?page_size=100&validation_status=pending', '/data-intake/jobs', '/annotations'],
  akim_obl: ['/ai/api/v1/scenarios', '/ai/api/v1/tasks?page_size=100', '/indicator-norms', '/landscape/runs'],
  akim_ray: ['/ai/api/v1/scenarios', '/ai/api/v1/tasks?page_size=100', '/indicator-values?page_size=100'],
}

export function setup() {
  const tokens = {}
  for (const [role, u, p] of ROLES) {
    const r = http.post(`${BASE}/auth/login`,
      JSON.stringify({ username: __ENV[u], password: __ENV[p] }),
      { headers: { 'Content-Type': 'application/json' } })
    check(r, { [`login ${role}`]: (x) => x.status === 200 })
    tokens[role] = r.json('access_token')
  }
  return tokens
}

export default function (tokens) {
  const role = ROLES[__VU % ROLES.length][0]
  const params = { headers: { Authorization: `Bearer ${tokens[role]}` }, tags: { role } }
  const paths = COMMON.concat(BY_ROLE[role])
  const path = paths[Math.floor(Math.random() * paths.length)]
  const r = http.get(BASE + path, Object.assign({}, params, { tags: { role, name: path.split('?')[0] } }))
  check(r, { 'status 200': (x) => x.status === 200 })
  sleep(1)
}
