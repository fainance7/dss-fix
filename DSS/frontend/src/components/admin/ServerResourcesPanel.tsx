'use client'

import { useEffect, useState } from 'react'
import {
  fetchModuleErrors,
  fetchServerResources,
  type ModuleError,
  type ServerResources,
} from '@/lib/api/backend'

// Загрузка сервера, нагрузка контейнеров Системы и журналы ошибок модулей (ТЗ ФFE.7).

const SERIES = [
  { key: 'cpu', name: 'ЦП', color: '#2563eb' },
  { key: 'memory', name: 'Память', color: '#16a34a' },
  { key: 'disk', name: 'Диск', color: '#d97706' },
] as const

const PERIODS = [
  { hours: 6, label: '6 ч' },
  { hours: 24, label: 'Сутки' },
  { hours: 168, label: 'Неделя' },
]

function levelColor(v: number | null | undefined): string {
  if (v == null) return 'text-gray-400'
  if (v >= 90) return 'text-red-600'
  if (v >= 75) return 'text-amber-600'
  return 'text-gray-900'
}

function LoadChart({ data, hours }: { data: NonNullable<ServerResources['series']>; hours: number }) {
  const W = 720
  const H = 200
  const L = 36
  const R = 8
  const T = 8
  const B = 24
  const all = SERIES.flatMap((s) => data[s.key])
  if (all.length === 0) return <p className="py-8 text-center text-sm text-gray-400">Нет данных за период</p>
  const t0 = Math.min(...all.map((p) => p[0]))
  const t1 = Math.max(...all.map((p) => p[0]))
  const x = (t: number) => L + ((t - t0) / Math.max(1, t1 - t0)) * (W - L - R)
  const y = (v: number) => T + (1 - v / 100) * (H - T - B)
  const fmt = (t: number) => {
    const d = new Date(t * 1000)
    return hours > 24
      ? d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })
      : d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  }
  const ticks = Array.from({ length: 6 }, (_, i) => t0 + ((t1 - t0) * i) / 5)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Загрузка сервера">
      {[0, 25, 50, 75, 100].map((v) => (
        <g key={v}>
          <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="#e5e7eb" />
          <text x={L - 6} y={y(v) + 4} textAnchor="end" fontSize="10" fill="#6b7280">{v}%</text>
        </g>
      ))}
      {ticks.map((t) => (
        <text key={t} x={x(t)} y={H - 6} textAnchor="middle" fontSize="10" fill="#6b7280">{fmt(t)}</text>
      ))}
      {SERIES.map((s) => {
        const pts = data[s.key]
        if (pts.length === 0) return null
        const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' ')
        return <path key={s.key} d={d} fill="none" stroke={s.color} strokeWidth={1.8} />
      })}
    </svg>
  )
}

export default function ServerResourcesPanel() {
  const [hours, setHours] = useState(24)
  const [res, setRes] = useState<ServerResources | null>(null)
  const [module, setModule] = useState<'backend' | 'ai-service'>('backend')
  const [errors, setErrors] = useState<ModuleError[] | null>(null)
  const [open, setOpen] = useState<number | null>(null)

  useEffect(() => {
    setRes(null)
    fetchServerResources(hours)
      .then(setRes)
      .catch(() => setRes({ available: false, detail: 'Не удалось получить метрики' }))
  }, [hours])

  useEffect(() => {
    setErrors(null)
    setOpen(null)
    fetchModuleErrors(module).then(setErrors).catch(() => setErrors([]))
  }, [module])

  return (
    <div className="mb-6 space-y-6">
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-5 py-4">
          <h2 className="text-sm font-semibold text-gray-900">Загрузка сервера</h2>
          <div className="flex gap-1">
            {PERIODS.map((p) => (
              <button
                key={p.hours}
                onClick={() => setHours(p.hours)}
                className={`rounded-md px-2.5 py-1 text-xs ${hours === p.hours ? 'bg-primary-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
        {!res ? (
          <p className="px-5 py-8 text-center text-sm text-gray-400">Загрузка…</p>
        ) : !res.available ? (
          <p className="px-5 py-8 text-center text-sm text-gray-500">{res.detail}</p>
        ) : (
          <div className="grid grid-cols-1 gap-6 p-5 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <div className="mb-3 grid grid-cols-3 gap-3">
                {SERIES.map((s) => (
                  <div key={s.key} className="rounded-lg bg-gray-50 px-3 py-2">
                    <div className="flex items-center gap-1.5 text-xs text-gray-500">
                      <span className="inline-block h-2 w-2 rounded-full" style={{ background: s.color }} />
                      {s.name}
                    </div>
                    <div className={`text-xl font-semibold ${levelColor(res.current?.[s.key])}`}>
                      {res.current?.[s.key] != null ? `${res.current[s.key]}%` : '—'}
                    </div>
                  </div>
                ))}
              </div>
              {res.series && <LoadChart data={res.series} hours={hours} />}
            </div>
            <div>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500">Контейнеры Системы</h3>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-gray-500">
                    <th className="pb-1 font-normal">Служба</th>
                    <th className="pb-1 text-right font-normal">ЦП</th>
                    <th className="pb-1 text-right font-normal">Память</th>
                  </tr>
                </thead>
                <tbody>
                  {(res.containers ?? []).map((c) => (
                    <tr key={c.name} className="border-t border-gray-50">
                      <td className="py-1 text-gray-700">{c.name.replace(/^dss-/, '')}</td>
                      <td className="py-1 text-right tabular-nums">{c.cpu_percent}%</td>
                      <td className="py-1 text-right tabular-nums">{c.memory_mb} МБ</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <div className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-5 py-4">
          <h2 className="text-sm font-semibold text-gray-900">Журнал ошибок модулей</h2>
          <div className="flex gap-1">
            {(['backend', 'ai-service'] as const).map((m) => (
              <button
                key={m}
                onClick={() => setModule(m)}
                className={`rounded-md px-2.5 py-1 text-xs ${module === m ? 'bg-primary-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
              >
                {m === 'backend' ? 'BackEnd и СХД' : 'Модуль ИИ'}
              </button>
            ))}
          </div>
        </div>
        {errors === null ? (
          <p className="px-5 py-6 text-center text-sm text-gray-400">Загрузка…</p>
        ) : errors.length === 0 ? (
          <p className="px-5 py-6 text-center text-sm text-gray-500">Ошибок не зарегистрировано</p>
        ) : (
          <ul className="max-h-80 divide-y divide-gray-50 overflow-y-auto">
            {errors.map((e, i) => (
              <li key={i} className="px-5 py-2 text-sm">
                <button className="w-full text-left" onClick={() => setOpen(open === i ? null : i)}>
                  <span className="mr-2 text-xs text-gray-400">{new Date(e.time).toLocaleString('ru-RU')}</span>
                  <span className="mr-2 text-xs font-medium text-red-600">{e.level}</span>
                  <span className="text-gray-800">{e.message}</span>
                  <span className="ml-2 text-xs text-gray-400">{e.logger}</span>
                </button>
                {open === i && e.trace && (
                  <pre className="mt-2 max-h-48 overflow-auto rounded bg-gray-50 p-2 text-xs text-gray-600">{e.trace}</pre>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
