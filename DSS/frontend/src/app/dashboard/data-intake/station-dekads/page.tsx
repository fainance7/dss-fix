'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import api from '@/lib/api'
import { useAuthStore } from '@/store/authStore'
import { fetchClimateStations, type ClimateStationsRef } from '@/lib/api/climate'

// Декадные значения метеостанций: просмотр и утверждение экспертом по проверке.
// После утверждения/отклонения декад месяцы станций и районов пересчитываются
// автоматически (температура — среднее, взвешенное по дням; осадки — сумма декад).

type Row = {
  id: string
  period_type: 'dekad' | 'month'
  period_start: string
  period_end: string
  dekad: number | null
  value: number
  validation_status: 'pending' | 'approved' | 'rejected'
  is_derived: boolean
  station: string
  region: string
  indicator: string
  unit: string | null
  job_id: string | null
  source_ref: string | null
}

type ListResp = { items: Row[]; total: number; page: number; pages: number }

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-700',
  approved: 'bg-green-100 text-green-700',
  rejected: 'bg-red-100 text-red-700',
}
const STATUS_RU: Record<string, string> = { pending: 'на проверке', approved: 'утверждено', rejected: 'отклонено' }
const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']

export default function StationDekadsPage() {
  const { hasRole } = useAuthStore()
  const canValidate = hasRole('admin') || hasRole('exp_ver')
  const [ref, setRef] = useState<ClimateStationsRef | null>(null)
  const [filters, setFilters] = useState({
    indicator: '',
    station_id: '',
    year: '',
    month: '',
    status: 'pending',
    period_type: 'dekad',
  })
  const [page, setPage] = useState(1)
  const [data, setData] = useState<ListResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [acting, setActing] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    fetchClimateStations().then(setRef).catch(() => setRef({ stations: [], regions: [] }))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setSelected(new Set())
    try {
      const params: Record<string, string | number> = { page, page_size: 200, period_type: filters.period_type }
      for (const k of ['indicator', 'station_id', 'year', 'month', 'status'] as const) {
        if (filters[k]) params[k] = filters[k]
      }
      const { data: resp } = await api.get<ListResp>('/climate/station-values', { params })
      setData(resp)
    } catch {
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [filters, page])

  useEffect(() => {
    load()
  }, [load])

  const isDekad = filters.period_type === 'dekad'
  const allChecked = useMemo(
    () => !!data && data.items.length > 0 && data.items.every((r) => selected.has(r.id)),
    [data, selected],
  )

  const validate = async (status: 'approved' | 'rejected') => {
    if (selected.size === 0) return
    setActing(true)
    setMessage(null)
    try {
      const { data: res } = await api.post<{ updated: number; recompute: Record<string, number> }>(
        '/climate/station-values/validate-bulk',
        { ids: Array.from(selected), status },
      )
      setMessage(
        `${status === 'approved' ? 'Утверждено' : 'Отклонено'} декад: ${res.updated}. ` +
          `Пересчитано месяцев станций: ${res.recompute?.station_months ?? 0}, районов: ${res.recompute?.region_months ?? 0}.`,
      )
      await load()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setMessage(err.response?.data?.detail ?? 'Ошибка при сохранении')
    } finally {
      setActing(false)
    }
  }

  const set = (k: keyof typeof filters, v: string) => {
    setPage(1)
    setFilters((f) => ({ ...f, [k]: v }))
  }

  return (
    <div className="space-y-4 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Декады метеостанций</h1>
        <p className="mt-1 text-sm text-gray-600">
          Средняя температура воздуха и осадки по метеостанциям за декаду (1–10, 11–20, 21–конец месяца).
          Месяц Система считает сама, когда есть все три декады: температура — среднее, взвешенное по
          числу дней, осадки — сумма декад. После утверждения или отклонения декад месяцы станций и
          районов пересчитываются автоматически.
        </p>
      </div>

      <section className="grid grid-cols-2 gap-3 rounded-lg border bg-white p-4 text-sm md:grid-cols-3 lg:grid-cols-6">
        <label>
          <div className="mb-0.5 text-xs text-gray-500">Показатель</div>
          <select className="w-full rounded border px-2 py-1" value={filters.indicator} onChange={(e) => set('indicator', e.target.value)}>
            <option value="">— все —</option>
            <option value="temperature">Температура воздуха</option>
            <option value="precipitation">Осадки</option>
          </select>
        </label>
        <label>
          <div className="mb-0.5 text-xs text-gray-500">Метеостанция</div>
          <select className="w-full rounded border px-2 py-1" value={filters.station_id} onChange={(e) => set('station_id', e.target.value)}>
            <option value="">— все —</option>
            {(ref?.stations ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <div className="mb-0.5 text-xs text-gray-500">Год</div>
          <input
            className="w-full rounded border px-2 py-1"
            type="number"
            placeholder="все"
            value={filters.year}
            onChange={(e) => set('year', e.target.value)}
          />
        </label>
        <label>
          <div className="mb-0.5 text-xs text-gray-500">Месяц</div>
          <select className="w-full rounded border px-2 py-1" value={filters.month} onChange={(e) => set('month', e.target.value)}>
            <option value="">— все —</option>
            {MONTHS.map((m, i) => (
              <option key={m} value={i + 1}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label>
          <div className="mb-0.5 text-xs text-gray-500">Статус</div>
          <select className="w-full rounded border px-2 py-1" value={filters.status} onChange={(e) => set('status', e.target.value)}>
            <option value="">— все —</option>
            <option value="pending">на проверке</option>
            <option value="approved">утверждено</option>
            <option value="rejected">отклонено</option>
          </select>
        </label>
        <label>
          <div className="mb-0.5 text-xs text-gray-500">Период</div>
          <select className="w-full rounded border px-2 py-1" value={filters.period_type} onChange={(e) => set('period_type', e.target.value)}>
            <option value="dekad">декады (исходные)</option>
            <option value="month">месяцы (расчётные)</option>
          </select>
        </label>
      </section>

      {canValidate && isDekad && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-gray-500">Выбрано: {selected.size}</span>
          <button
            onClick={() => validate('approved')}
            disabled={acting || selected.size === 0}
            className="rounded-lg bg-green-600 px-3 py-1.5 text-white disabled:bg-gray-300"
          >
            Утвердить
          </button>
          <button
            onClick={() => validate('rejected')}
            disabled={acting || selected.size === 0}
            className="rounded-lg bg-red-600 px-3 py-1.5 text-white disabled:bg-gray-300"
          >
            Отклонить
          </button>
          {message && <span className="text-gray-700">{message}</span>}
        </div>
      )}

      <section className="rounded-lg border bg-white">
        <div className="overflow-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                {canValidate && isDekad && (
                  <th className="w-8 px-2 py-2">
                    <input
                      type="checkbox"
                      checked={allChecked}
                      onChange={() =>
                        setSelected(allChecked ? new Set() : new Set((data?.items ?? []).map((r) => r.id)))
                      }
                    />
                  </th>
                )}
                <th className="px-3 py-2 text-left">Метеостанция</th>
                <th className="px-3 py-2 text-left">Район</th>
                <th className="px-3 py-2 text-left">Показатель</th>
                <th className="px-3 py-2 text-left">Период</th>
                <th className="px-3 py-2 text-right">Значение</th>
                <th className="px-3 py-2 text-left">Статус</th>
                <th className="px-3 py-2 text-left">Партия</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={8} className="px-3 py-8 text-center text-gray-500">
                    Загрузка…
                  </td>
                </tr>
              )}
              {!loading && data?.items.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3 py-8 text-center text-gray-500">
                    Нет значений под фильтр
                  </td>
                </tr>
              )}
              {!loading &&
                data?.items.map((r) => (
                  <tr key={r.id} className="border-t hover:bg-gray-50">
                    {canValidate && isDekad && (
                      <td className="px-2 py-1.5">
                        <input
                          type="checkbox"
                          checked={selected.has(r.id)}
                          onChange={() => {
                            const next = new Set(selected)
                            if (next.has(r.id)) next.delete(r.id)
                            else next.add(r.id)
                            setSelected(next)
                          }}
                        />
                      </td>
                    )}
                    <td className="px-3 py-1.5">{r.station}</td>
                    <td className="px-3 py-1.5 text-gray-600">{r.region}</td>
                    <td className="px-3 py-1.5">{r.indicator}</td>
                    <td className="whitespace-nowrap px-3 py-1.5">
                      {MONTHS[Number(r.period_start.slice(5, 7)) - 1]} {r.period_start.slice(0, 4)}
                      {r.dekad ? `, ${r.dekad}-я декада` : ' (месяц)'}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {r.value.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} {r.unit}
                    </td>
                    <td className="px-3 py-1.5">
                      <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_BADGE[r.validation_status] ?? ''}`}>
                        {STATUS_RU[r.validation_status]}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-xs">
                      {r.job_id ? (
                        <a href={`/dashboard/data-intake/jobs/${r.job_id}`} className="text-primary-700 hover:underline">
                          {r.source_ref ?? 'партия'}
                        </a>
                      ) : (
                        <span className="text-gray-400">{r.is_derived ? 'расчёт' : 'историческая загрузка'}</span>
                      )}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        {data && data.pages > 1 && (
          <div className="flex items-center justify-between border-t px-4 py-2 text-sm">
            <span className="text-gray-500">
              {data.total} всего · стр. {data.page} из {data.pages}
            </span>
            <div className="flex gap-1">
              <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="rounded border px-3 py-1 text-xs disabled:opacity-50">
                ←
              </button>
              <button onClick={() => setPage((p) => Math.min(data.pages, p + 1))} disabled={page === data.pages} className="rounded border px-3 py-1 text-xs disabled:opacity-50">
                →
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
