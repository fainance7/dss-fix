/** @type {import('next').NextConfig} */

// Источники, к которым обращается браузер: API Системы, подложка OpenFreeMap
// (стиль, тайлы, шрифты подписей) и локальный NCALayer для входа по ЭЦП.
const apiOrigin = (() => {
  try {
    return new URL(process.env.NEXT_PUBLIC_API_URL ?? '').origin
  } catch {
    return '' // относительный /api/v1 — тот же источник, что и страница
  }
})()
const BASEMAP = 'https://tiles.openfreemap.org'
const NCALAYER = 'wss://127.0.0.1:13579 ws://127.0.0.1:13579'

// CSP — только в production-сборке: next dev использует eval для горячей перезагрузки.
// 'unsafe-inline' в script-src нужен встроенным скриптам Next.js без nonce.
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${BASEMAP} ${apiOrigin}`,
  "font-src 'self' data:",
  `connect-src 'self' ${apiOrigin} ${BASEMAP} ${NCALAYER}`,
  "worker-src 'self' blob:",
  "child-src blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ')

// Заголовки безопасности страниц (аудит безопасности, L-4).
const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
  ...(process.env.NODE_ENV === 'production'
    ? [
        { key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' },
        { key: 'Content-Security-Policy', value: csp },
      ]
    : []),
]

const nextConfig = {
  output: 'standalone',
  poweredByHeader: false,
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }]
  },
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8000'
    // Модуль ИИ — только через прокси бэкенда (/api/v1/ai/*): он проверяет вход и
    // роли и сам подставляет ключ ai-service. Прямого пути /ai/* на ai-service нет.
    return [
      {
        source: '/api/v1/:path*',
        destination: `${backendUrl}/api/v1/:path*`,
      },
    ]
  },
}

module.exports = nextConfig
