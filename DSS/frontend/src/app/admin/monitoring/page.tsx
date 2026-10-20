'use client'

import { useState, useEffect, useMemo, useCallback } from 'react'
import { useAuthStore } from '@/store/authStore'
import Link from 'next/link'
import ServerResourcesPanel from '@/components/admin/ServerResourcesPanel'
import {
  fetchSdMetrics,
  fetchSystemMetrics,
  fetchForecastHistory,
  fetchAuditLog,
  fetchAuditActions,
  exportAuditLog,
} from '@/lib/api/backend'
import type {
  SdMonitoringMetrics,
  SystemMetrics,
  ForecastRun,
  AuditLogItem,
} from '@/lib/api/backend'

interface MetricBar {
  label: string
  value: number
  unit: string
  color: string
}

const ACTION_COLORS: Record<string, string> = {
  login: 'bg-gray-100 text-gray-600',
  create: 'bg-green-100 text-green-700',
  update: 'bg-blue-100 text-blue-700',
  delete: 'bg-red-100 text-red-700',
  approve: 'bg-primary-100 text-primary-700',
  validate: 'bg-teal-100 text-teal-700',
  reject: 'bg-orange-100 text-orange-700',
  upload: 'bg-indigo-100 text-indigo-700',
}

function actionColor(action: string): string {
  const key = action.toLowerCase()
  for (const k of Object.keys(ACTION_COLORS)) {
    if (key.includes(k)) return ACTION_COLORS[k]
  }
  return 'bg-gray-100 text-gray-600'
}

const PAGE_SIZE = 8

function formatDetails(details: Record<string, unknown> | null | undefined): string {
  if (!details) return ''
  return Object.entries(details)
    .map(([k, v]) => `${k}: ${typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)}`)
    .join(', ')
}

export default function AdminPage() {
  const { hasRole } = useAuthStore()
  const isAdmin = hasRole('admin')

  if (!isAdmin && !hasRole('data_uploader')) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <div className="mb-4 text-5xl">&#128274;</div>
        <h2 className="text-xl font-semibold text-gray-900">Доступ запрещён</h2>
        <p className="mt-2 text-sm text-gray-500">
          Эта страница доступна только администраторам и загрузчикам данных.
        </p>
      </div>
    )
  }

  return <AdminContent full={isAdmin} />
}

function SkeletonMetric() {
  return (
    <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100 animate-pulse">
      <div className="mb-2 flex items-center justify-between">
        <div className="h-3 w-20 rounded bg-gray-200" />
        <div className="h-3 w-10 rounded bg-gray-200" />
      </div>
      <div className="h-2 w-full rounded-full bg-gray-200" />
    </div>
  )
}

// full=false — режим data_uploader: только журнал загрузок (бэкенд отдаёт записи о приёме данных).
function AdminContent({ full }: { full: boolean }) {
  const [metrics, setMetrics] = useState<SdMonitoringMetrics | null>(null)
  const [system, setSystem] = useState<SystemMetrics | null>(null)
  const [forecasts, setForecasts] = useState<ForecastRun[]>([])
  const [metricsLoading, setMetricsLoading] = useState(true)

  // Журнал аудита (серверная фильтрация + пагинация)
  const [auditItems, setAuditItems] = useState<AuditLogItem[]>([])
  const [auditTotal, setAuditTotal] = useState(0)
  const [auditActions, setAuditActions] = useState<string[]>([])
  const [actionFilter, setActionFilter] = useState('Все')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [auditLoading, setAuditLoading] = useState(true)

  useEffect(() => {
    fetchAuditActions().then(setAuditActions).catch(() => setAuditActions([]))
    if (!full) {
      setMetricsLoading(false)
      return
    }
    Promise.allSettled([fetchSdMetrics(), fetchSystemMetrics(), fetchForecastHistory(15)])
      .then(([sd, sys, fc]) => {
        if (sd.status === 'fulfilled') setMetrics(sd.value)
        if (sys.status === 'fulfilled') setSystem(sys.value)
        if (fc.status === 'fulfilled') setForecasts(fc.value)
      })
      .finally(() => setMetricsLoading(false))
  }, [full])

  const loadAudit = useCallback(() => {
    setAuditLoading(true)
    fetchAuditLog({
      action: actionFilter !== 'Все' ? actionFilter : undefined,
      page,
      page_size: PAGE_SIZE,
    })
      .then((res) => {
        setAuditItems(res.items)
        setAuditTotal(res.total)
      })
      .catch(() => {
        setAuditItems([])
        setAuditTotal(0)
      })
      .finally(() => setAuditLoading(false))
  }, [actionFilter, page])

  useEffect(() => {
    loadAudit()
  }, [loadAudit])

  // Метрики наполнения СХД (реальные данные валидации)
  const metricBars: MetricBar[] = useMemo(() => {
    if (!metrics) return []
    const total = metrics.total_spatial_records || 1
    const approvedPct = Math.round((metrics.approved_records / total) * 100)
    const pendingPct = Math.round((metrics.pending_validation / total) * 100)
    const rejectedPct = Math.round((metrics.rejected_records / total) * 100)
    const activeLayersPct = Math.min(Math.round((metrics.active_layers / (metrics.total_layers || 1)) * 100), 100)
    return [
      { label: 'Подтверждено', value: approvedPct, unit: '%', color: 'bg-green-500' },
      { label: 'На проверке', value: pendingPct, unit: '%', color: 'bg-yellow-500' },
      { label: 'Отклонено', value: rejectedPct, unit: '%', color: 'bg-red-500' },
      { label: 'Активных слоёв', value: activeLayersPct, unit: '%', color: 'bg-blue-500' },
    ]
  }, [metrics])

  // Текстовый фильтр применяется к текущей странице (пользователь/объект)
  const visibleAudit = useMemo(() => {
    if (!search) return auditItems
    const q = search.toLowerCase()
    return auditItems.filter(
      (e) =>
        (e.username ?? '').toLowerCase().includes(q) ||
        e.entity_type.toLowerCase().includes(q),
    )
  }, [auditItems, search])

  const totalPages = Math.max(1, Math.ceil(auditTotal / PAGE_SIZE))

  async function exportCSV() {
    // Все записи по выбранному фильтру, а не только текущая страница.
    const blob = await exportAuditLog({ action: actionFilter !== 'Все' ? actionFilter : undefined })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `audit-log${actionFilter !== 'Все' ? `-${actionFilter}` : ''}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <>
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-gray-900">{full ? 'Администрирование' : 'Журнал загрузок'}</h1>
        <p className="mt-0.5 text-sm text-gray-500">
          {full
            ? 'Системные метрики и журнал аудита'
            : 'Загрузки, перепроверки и импорт партий данных; ошибки строк — в карточке партии'}
        </p>
      </div>

      {full && (
      <>

      {/* СХД Summary cards */}
      {metrics && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {[
            { label: 'Пространств. данных', value: (metrics.total_spatial_records ?? 0).toLocaleString('ru-RU'), color: 'text-gray-900' },
            { label: 'Подтверждено', value: (metrics.approved_records ?? 0).toLocaleString('ru-RU'), color: 'text-green-600' },
            { label: 'На проверке', value: (metrics.pending_validation ?? 0).toLocaleString('ru-RU'), color: 'text-yellow-600' },
            { label: 'Отклонено', value: (metrics.rejected_records ?? 0).toLocaleString('ru-RU'), color: 'text-red-600' },
            { label: 'Слоёв данных', value: String(metrics.total_layers ?? 0), color: 'text-blue-600' },
            { label: 'Аннотаций', value: String(metrics.total_annotations ?? 0), color: 'text-purple-600' },
          ].map((m) => (
            <div key={m.label} className="rounded-xl bg-white p-3 shadow-sm ring-1 ring-gray-100 text-center">
              <div className={`text-xl font-bold ${m.color}`}>{m.value}</div>
              <div className="mt-0.5 text-xs text-gray-500">{m.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Наполнение СХД (валидация) */}
      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {metricsLoading
          ? Array.from({ length: 4 }).map((_, i) => <SkeletonMetric key={i} />)
          : metricBars.map((m) => (
              <div key={m.label} className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
                <div className="mb-2 flex items-center justify-between text-sm">
                  <span className="font-medium text-gray-700">{m.label}</span>
                  <span className="text-gray-500">
                    {m.value}
                    {m.unit}
                  </span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
                  <div
                    className={`h-full rounded-full transition-all ${m.color}`}
                    style={{ width: `${m.value}%` }}
                  />
                </div>
              </div>
            ))}
      </div>

      {/* Services + DB size + Quick link */}
      <div className="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Services grid */}
        <div className="lg:col-span-2 rounded-xl bg-white shadow-sm ring-1 ring-gray-100 overflow-hidden">
          <div className="flex items-center justify-between border-b border-gray-100 px-5 py-4">
            <h2 className="text-sm font-semibold text-gray-900">Статус сервисов</h2>
            {system && (
              <span className="text-xs text-gray-400">
                БД: {system.db_size_pretty}
              </span>
            )}
          </div>
          {metricsLoading ? (
            <div className="p-6 text-sm text-gray-400">Загрузка…</div>
          ) : (
            <div className="grid grid-cols-2 gap-px bg-gray-100 sm:grid-cols-3">
              {(system?.services ?? []).map((svc) => (
                <div key={svc.name} className="flex flex-col gap-1 bg-white p-4">
                  <div className="flex items-center gap-2">
                    <span
                      className={`h-2.5 w-2.5 rounded-full flex-shrink-0 ${
                        svc.status === 'up' ? 'bg-green-500' : 'bg-red-500'
                      }`}
                    />
                    <span className="text-sm font-medium text-gray-800">{svc.name}</span>
                  </div>
                  <div className="text-xs text-gray-400 pl-4">
                    {svc.status === 'up'
                      ? svc.latency_ms != null
                        ? `${svc.latency_ms} мс`
                        : 'работает'
                      : svc.detail ?? 'недоступен'}
                  </div>
                </div>
              ))}
              {(!system || system.services.length === 0) && (
                <div className="col-span-full p-4 text-sm text-gray-400">Нет данных о сервисах</div>
              )}
            </div>
          )}
        </div>

        {/* Quick links */}
        <div className="space-y-4">
          <Link
            href="/admin/users"
            className="block rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100 hover:shadow-md transition-shadow"
          >
            <div className="mb-1 text-2xl">&#128101;</div>
            <div className="font-semibold text-gray-900">Пользователи</div>
            <div className="mt-1 text-sm text-gray-500">
              Управление учётными записями и ролями
            </div>
            <div className="mt-3 text-xs font-medium text-primary-600">Перейти →</div>
          </Link>
          <Link
            href="/admin/settings"
            className="block rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100 hover:shadow-md transition-shadow"
          >
            <div className="mb-1 text-2xl">&#9881;&#65039;</div>
            <div className="font-semibold text-gray-900">Настройки</div>
            <div className="mt-1 text-sm text-gray-500">
              Источники данных, резервное копирование
            </div>
            <div className="mt-3 text-xs font-medium text-primary-600">Перейти →</div>
          </Link>
        </div>
      </div>

      {/* Загрузка сервера, контейнеры, журналы ошибок модулей (ФFE.7) */}
      {full && <ServerResourcesPanel />}

      {/* Forecast history */}
      <div className="mb-6 rounded-xl bg-white shadow-sm ring-1 ring-gray-100 overflow-hidden">
        <div className="border-b border-gray-100 px-5 py-4">
          <h2 className="text-sm font-semibold text-gray-900">История прогнозов (Модуль ИИ)</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Дата расчёта</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Показатель</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Регион</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Статус</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Создан</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {forecasts.map((f) => (
                <tr key={f.id} className="hover:bg-gray-50/50">
                  <td className="px-4 py-3 text-gray-600 whitespace-nowrap">{f.forecast_date ?? '—'}</td>
                  <td className="px-4 py-3 text-gray-500 font-mono text-xs">{f.indicator_id?.slice(0, 8) ?? '—'}</td>
                  <td className="px-4 py-3 text-gray-500 font-mono text-xs">{f.region_id?.slice(0, 8) ?? '—'}</td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${actionColor(f.status)}`}>
                      {f.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-400 whitespace-nowrap text-xs">
                    {f.created_at ? new Date(f.created_at).toLocaleString('ru-RU') : '—'}
                  </td>
                </tr>
              ))}
              {forecasts.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-gray-400">
                    {metricsLoading ? 'Загрузка…' : 'Прогнозы ещё не запускались'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      </>
      )}

      {/* Audit log */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
          <h2 className="text-sm font-semibold text-gray-900">{full ? 'Журнал аудита' : 'События загрузки'}</h2>
          <div className="flex flex-wrap gap-2">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Пользователь / объект (на странице)…"
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
            <select
              value={actionFilter}
              onChange={(e) => {
                setActionFilter(e.target.value)
                setPage(1)
              }}
              className="rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-700 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              {['Все', ...auditActions].map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <button
              onClick={exportCSV}
              className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
            >
              ↓ CSV
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Время</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Пользователь</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Действие</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Объект</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">ID объекта</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Детали</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">IP</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {visibleAudit.map((entry, idx) => (
                <tr
                  key={entry.id}
                  className={idx % 2 === 0 ? 'hover:bg-gray-50/50' : 'bg-gray-50/30 hover:bg-gray-50'}
                >
                  <td className="px-4 py-3 text-gray-500 whitespace-nowrap font-mono text-xs">
                    {new Date(entry.created_at).toLocaleString('ru-RU')}
                  </td>
                  <td className="px-4 py-3 font-medium text-gray-800">{entry.username ?? '—'}</td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${actionColor(entry.action)}`}>
                      {entry.action}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-600">{entry.entity_type}</td>
                  <td className="px-4 py-3 text-gray-400 font-mono text-xs">
                    {entry.entity_type === 'data_intake_job' && entry.entity_id ? (
                      <Link href={`/dashboard/data-intake/jobs/${entry.entity_id}`} className="text-primary-600 hover:underline">
                        {entry.entity_id.slice(0, 8)}
                      </Link>
                    ) : (
                      entry.entity_id?.slice(0, 8) ?? '—'
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600 max-w-[320px] truncate" title={formatDetails(entry.details)}>
                    {formatDetails(entry.details)}
                  </td>
                  <td className="px-4 py-3 text-gray-400 font-mono text-xs">{entry.ip_address ?? '—'}</td>
                </tr>
              ))}
              {visibleAudit.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-10 text-center text-gray-400">
                    {auditLoading ? 'Загрузка…' : 'Записи не найдены'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between border-t border-gray-100 px-5 py-3">
            <span className="text-xs text-gray-400">
              {auditTotal} записей · стр. {page} из {totalPages}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm disabled:opacity-40 hover:bg-gray-50 disabled:cursor-not-allowed"
              >
                ‹
              </button>
              <span className="rounded-lg border border-primary-500 bg-primary-600 px-3 py-1.5 text-sm text-white">
                {page}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm disabled:opacity-40 hover:bg-gray-50 disabled:cursor-not-allowed"
              >
                ›
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
