'use client'

import { useState } from 'react'

// Отчёт контроля качества партии приёма данных (GET /data-intake/jobs/{id}/quality).
// Проверки и формулировки — backend/app/services/intake_quality.py.

export type QualitySeverity = 'blocker' | 'warning' | 'info'

export type QualityFinding = {
  code: string
  severity: QualitySeverity
  title: string
  message: string
  count: number
  items: Record<string, unknown>[]
}

export type QualityReportData = {
  generated_at?: string
  rows_checked?: number
  summary: { blocker: number; warning: number; info: number }
  findings: QualityFinding[]
  acknowledged?: { username: string; at: string; comment: string }
  not_computed?: boolean
}

export const SEVERITY_STYLE: Record<QualitySeverity, { label: string; badge: string; box: string }> = {
  blocker: { label: 'блокер', badge: 'bg-red-100 text-red-700', box: 'border-red-200 bg-red-50' },
  warning: { label: 'предупреждение', badge: 'bg-amber-100 text-amber-800', box: 'border-amber-200 bg-amber-50' },
  info: { label: 'к сведению', badge: 'bg-gray-100 text-gray-600', box: 'border-gray-200 bg-gray-50' },
}

// Подписи колонок таблицы примеров; служебные поля не показываются.
const COLUMN_LABELS: Record<string, string> = {
  indicator: 'Показатель',
  region: 'Регион',
  year: 'Год',
  month: 'Месяц',
  dekad: 'Декада',
  prev_year: 'Пред. год',
  old: 'Было',
  prev: 'Пред. значение',
  new: 'Новое',
  cur: 'Значение',
  ratio: 'Отношение',
  change_pct: 'Изменение, %',
  median: 'Медиана ряда',
  oblast: 'Область',
  districts_sum: 'Сумма районов',
  share_pct: 'Доля, %',
  districts: 'Районов',
  rows: 'Строк',
  values: 'Значения',
  source: 'Источник',
  source_ref: 'Партия',
  status: 'Статус',
  uploaded_at: 'Загружена',
}
const HIDDEN = new Set(['period_start', 'unit', 'job_id'])

// Порядок колонок: сначала что и где, потом значения.
const COLUMN_ORDER = Object.keys(COLUMN_LABELS)
const PLAIN_NUMBERS = new Set(['year', 'prev_year', 'month', 'dekad'])

function fmt(v: unknown, col = ''): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number' && PLAIN_NUMBERS.has(col)) return String(v)
  if (typeof v === 'number') return v.toLocaleString('ru-RU', { maximumFractionDigits: 3 })
  if (Array.isArray(v)) return v.map((x) => fmt(x)).join('; ')
  if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v)) return new Date(v).toLocaleString('ru-RU')
  return String(v)
}

function Finding({ f }: { f: QualityFinding }) {
  const [open, setOpen] = useState(f.severity === 'blocker')
  const st = SEVERITY_STYLE[f.severity]
  const cols = f.items.length
    ? Object.keys(f.items[0])
        .filter((k) => !HIDDEN.has(k))
        .sort((x, y) => (COLUMN_ORDER.indexOf(x) + 1 || 99) - (COLUMN_ORDER.indexOf(y) + 1 || 99))
    : []
  return (
    <div className={`rounded-lg border p-3 ${st.box}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${st.badge}`}>{st.label}</span>
        <span className="text-sm font-medium text-gray-900">{f.title}</span>
        <span className="text-xs text-gray-500">· {f.count}</span>
        {f.items.length > 0 && (
          <button type="button" onClick={() => setOpen((v) => !v)} className="ml-auto text-xs text-primary-700 hover:underline">
            {open ? 'скрыть' : 'подробнее'}
          </button>
        )}
      </div>
      <p className="mt-1 text-xs text-gray-700">{f.message}</p>
      {open && f.items.length > 0 && (
        <div className="mt-2 max-h-72 overflow-auto rounded border bg-white">
          <table className="min-w-full text-xs">
            <thead className="sticky top-0 bg-gray-50">
              <tr>
                {cols.map((c) => (
                  <th key={c} className="px-2 py-1 text-left font-medium text-gray-600">
                    {COLUMN_LABELS[c] ?? c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {f.items.map((it, i) => (
                <tr key={i} className="border-t">
                  {cols.map((c) => (
                    <td key={c} className={`px-2 py-1 ${typeof it[c] === 'number' && !PLAIN_NUMBERS.has(c) ? 'text-right font-mono' : ''}`}>
                      {fmt(it[c], c)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {f.count > f.items.length && (
            <div className="border-t px-2 py-1 text-xs text-gray-500">
              показаны первые {f.items.length} из {f.count}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function QualityReport({
  report,
  loading,
  onRefresh,
  canRefresh,
}: {
  report: QualityReportData | null
  loading: boolean
  onRefresh: () => void
  canRefresh: boolean
}) {
  const s = report?.summary
  return (
    <section className="space-y-3 rounded-lg border bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium">Контроль качества данных</h2>
        {s && (
          <div className="flex gap-1.5 text-xs">
            {(['blocker', 'warning', 'info'] as QualitySeverity[]).map((k) =>
              s[k] ? (
                <span key={k} className={`rounded-full px-2 py-0.5 ${SEVERITY_STYLE[k].badge}`}>
                  {SEVERITY_STYLE[k].label}: {s[k]}
                </span>
              ) : null,
            )}
          </div>
        )}
        {canRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="ml-auto rounded-lg border border-primary-300 bg-white px-3 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-50 disabled:opacity-50"
          >
            {loading ? 'проверка…' : 'Проверить заново'}
          </button>
        )}
      </div>

      {loading && !report && <p className="text-xs text-gray-500">Проверка партии…</p>}
      {report?.not_computed && (
        <p className="text-xs text-gray-500">Отчёт для этой партии не формировался (импортирована до появления проверки).</p>
      )}
      {report && !report.not_computed && report.findings.length === 0 && (
        <p className="rounded-lg bg-green-50 p-2.5 text-xs text-green-800">
          Замечаний нет: повторной загрузки, совпадающих ключей, признаков смены единиц, обрывов ряда и
          расхождений районов с областью не найдено.
        </p>
      )}
      {report?.findings.map((f) => <Finding key={f.code} f={f} />)}

      {report?.acknowledged && (
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-2.5 text-xs text-gray-700">
          Импорт подтверждён экспертом <b>{report.acknowledged.username}</b>{' '}
          {new Date(report.acknowledged.at).toLocaleString('ru-RU')}: «{report.acknowledged.comment}»
        </div>
      )}
      {report?.generated_at && (
        <p className="text-[11px] text-gray-400">
          Проверено строк: {report.rows_checked ?? '—'} · {new Date(report.generated_at).toLocaleString('ru-RU')}
          {report.acknowledged || !canRefresh ? ' · снимок перед импортом' : ''}
        </p>
      )}
    </section>
  )
}
