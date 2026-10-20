'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import api from '@/lib/api'
import { useAuthStore } from '@/store/authStore'
import { createAnnotation, fetchAnnotations, type MapAnnotation } from '@/lib/api/backend'

type Indicator = {
  id: string
  name: string
  direction_name?: string | null
  unit_name?: string | null
}

type Region = {
  id: string
  name: string
  level?: number
}

type ValueRow = {
  id: string
  indicator_id: string
  indicator_name?: string | null
  region_id: string
  region_name?: string | null
  period_type: string
  period_start: string
  period_end: string
  value: number
  validation_status: 'pending' | 'approved' | 'rejected'
  validated_at?: string | null
  quality_flags?: QualityFlag[]
}

// Замечание контроля качества из последней импортированной партии с этим значением.
type QualityFlag = {
  code: string
  severity: 'blocker' | 'warning' | 'info'
  message: string
  job_id: string
  source_ref: string
}

const FLAG_STYLE: Record<string, string> = {
  blocker: 'bg-red-100 text-red-700',
  warning: 'bg-amber-100 text-amber-800',
  info: 'bg-gray-100 text-gray-600',
}

type ListResp = {
  items: ValueRow[]
  total: number
  page: number
  page_size: number
  pages: number
}

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-700',
  approved: 'bg-green-100 text-green-700',
  rejected: 'bg-red-100 text-red-700',
}

export default function IndicatorValuesPage() {
  const { hasRole } = useAuthStore()
  const canValidate = hasRole('admin') || hasRole('exp_ver')
  // Комментарии и отметки к значениям (ТЗ ФFE.1, Прил. 2 ч. 1): эксперты и администратор.
  const canAnnotate = canValidate || hasRole('exp_dir')
  const [notes, setNotes] = useState<Record<string, MapAnnotation[]>>({})
  const [noteFor, setNoteFor] = useState<ValueRow | null>(null)
  const [noteText, setNoteText] = useState('')
  const [noteSaving, setNoteSaving] = useState(false)

  const [indicators, setIndicators] = useState<Indicator[]>([])
  const [regions, setRegions] = useState<Region[]>([])

  const [filters, setFilters] = useState({
    indicator_id: '',
    region_id: '',
    period_type: '',
    date_from: '',
    date_to: '',
    validation_status: '',
    has_quality_flags: false,
  })
  const [page, setPage] = useState(1)
  const pageSize = 100
  const [data, setData] = useState<ListResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [acting, setActing] = useState(false)

  // initial load: справочники
  useEffect(() => {
    api
      .get<Indicator[]>('/indicators?is_active=true')
      .then((r) => setIndicators(Array.isArray(r.data) ? r.data : []))
      .catch(() => setIndicators([]))
    api
      .get<Region[] | { items: Region[] }>('/regions')
      .then((r) => {
        const d = r.data
        setRegions(Array.isArray(d) ? d : d.items ?? [])
      })
      .catch(() => setRegions([]))
  }, [])

  const loadValues = useCallback(async () => {
    setLoading(true)
    setSelected(new Set())
    try {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      if (filters.indicator_id) params.set('indicator_id', filters.indicator_id)
      if (filters.region_id) params.set('region_id', filters.region_id)
      if (filters.period_type) params.set('period_type', filters.period_type)
      if (filters.date_from) params.set('date_from', filters.date_from)
      if (filters.date_to) params.set('date_to', filters.date_to)
      if (filters.validation_status) params.set('validation_status', filters.validation_status)
      if (filters.has_quality_flags) params.set('has_quality_flags', 'true')
      const { data: resp } = await api.get<ListResp>(`/indicator-values?${params}`)
      setData(resp)
      if (canAnnotate && resp.items.length) {
        const list = await fetchAnnotations({ indicatorValueIds: resp.items.map((i) => i.id) }).catch(() => [])
        const byValue: Record<string, MapAnnotation[]> = {}
        for (const a of list) {
          if (a.indicator_value_id) (byValue[a.indicator_value_id] ??= []).push(a)
        }
        setNotes(byValue)
      }
    } catch {
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [page, filters, canAnnotate])

  useEffect(() => {
    loadValues()
  }, [loadValues])

  const [exporting, setExporting] = useState(false)
  async function exportXlsx() {
    setExporting(true)
    try {
      const params: Record<string, string> = {}
      if (filters.indicator_id) params.indicator_id = filters.indicator_id
      if (filters.region_id) params.region_id = filters.region_id
      if (filters.period_type) params.period_type = filters.period_type
      if (filters.date_from) params.date_from = filters.date_from
      if (filters.date_to) params.date_to = filters.date_to
      if (filters.validation_status) params.validation_status = filters.validation_status
      const { data: blob } = await api.get<Blob>('/indicator-values/export', { params, responseType: 'blob' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'показатели.xlsx'
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setExporting(false)
    }
  }

  async function saveNote() {
    if (!noteFor || !noteText.trim()) return
    setNoteSaving(true)
    try {
      const a = await createAnnotation({ comment: noteText.trim(), indicator_value_id: noteFor.id })
      setNotes((prev) => ({ ...prev, [noteFor.id]: [a, ...(prev[noteFor.id] ?? [])] }))
      setNoteFor(null)
      setNoteText('')
    } finally {
      setNoteSaving(false)
    }
  }

  const allChecked = useMemo(
    () => !!data && data.items.length > 0 && data.items.every((i) => selected.has(i.id)),
    [data, selected],
  )

  const toggleAll = () => {
    if (!data) return
    if (allChecked) {
      setSelected(new Set())
    } else {
      setSelected(new Set(data.items.map((i) => i.id)))
    }
  }

  const toggleOne = (id: string) => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelected(next)
  }

  const validate = async (status: 'approved' | 'rejected') => {
    if (selected.size === 0) {
      alert('Выберите строки для утверждения / отклонения')
      return
    }
    if (!confirm(`Установить статус "${status}" для ${selected.size} значений?`)) return
    setActing(true)
    try {
      const { data: res } = await api.post<{ updated: number; not_found: number }>(
        '/indicator-values/validate-bulk',
        { ids: Array.from(selected), status },
      )
      alert(`Обновлено: ${res.updated}` + (res.not_found ? `\nне найдено: ${res.not_found}` : ''))
      await loadValues()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      alert(err.response?.data?.detail ?? 'Ошибка валидации')
    } finally {
      setActing(false)
    }
  }

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Импортированные значения</h1>
        <p className="mt-1 text-sm text-gray-600">
          Просмотр indicator_values, фильтрация и утверждение/отклонение для обучения моделей.
        </p>
      </div>

      {/* Фильтры */}
      <section className="rounded-lg border bg-white p-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6 text-sm">
        <label>
          <div className="text-xs text-gray-500 mb-0.5">Показатель</div>
          <select
            className="w-full rounded border px-2 py-1"
            value={filters.indicator_id}
            onChange={(e) => {
              setPage(1)
              setFilters((f) => ({ ...f, indicator_id: e.target.value }))
            }}
          >
            <option value="">— все —</option>
            {indicators.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <div className="text-xs text-gray-500 mb-0.5">Регион</div>
          <select
            className="w-full rounded border px-2 py-1"
            value={filters.region_id}
            onChange={(e) => {
              setPage(1)
              setFilters((f) => ({ ...f, region_id: e.target.value }))
            }}
          >
            <option value="">— все —</option>
            {regions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <div className="text-xs text-gray-500 mb-0.5">Период</div>
          <select
            className="w-full rounded border px-2 py-1"
            value={filters.period_type}
            onChange={(e) => {
              setPage(1)
              setFilters((f) => ({ ...f, period_type: e.target.value }))
            }}
          >
            <option value="">— все —</option>
            <option value="year">year</option>
            <option value="quarter">quarter</option>
            <option value="month">month</option>
            <option value="day">day</option>
          </select>
        </label>
        <label>
          <div className="text-xs text-gray-500 mb-0.5">С даты</div>
          <input
            type="date"
            className="w-full rounded border px-2 py-1"
            value={filters.date_from}
            onChange={(e) => {
              setPage(1)
              setFilters((f) => ({ ...f, date_from: e.target.value }))
            }}
          />
        </label>
        <label>
          <div className="text-xs text-gray-500 mb-0.5">По дату</div>
          <input
            type="date"
            className="w-full rounded border px-2 py-1"
            value={filters.date_to}
            onChange={(e) => {
              setPage(1)
              setFilters((f) => ({ ...f, date_to: e.target.value }))
            }}
          />
        </label>
        <label>
          <div className="text-xs text-gray-500 mb-0.5">Статус</div>
          <select
            className="w-full rounded border px-2 py-1"
            value={filters.validation_status}
            onChange={(e) => {
              setPage(1)
              setFilters((f) => ({ ...f, validation_status: e.target.value }))
            }}
          >
            <option value="">— все —</option>
            <option value="pending">pending</option>
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
          </select>
        </label>
        <label className="col-span-2 flex items-center gap-2 self-end pb-1 text-sm text-gray-700 whitespace-nowrap">
          <input
            type="checkbox"
            checked={filters.has_quality_flags}
            onChange={(e) => {
              setPage(1)
              setFilters((f) => ({ ...f, has_quality_flags: e.target.checked }))
            }}
          />
          только с замечаниями проверки
        </label>
      </section>

      {/* Отчёт по показателям в Excel — по текущим фильтрам (ТЗ Прил. 2, ч. 2) */}
      <div className="flex justify-end">
        <button
          onClick={exportXlsx}
          disabled={exporting}
          className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          {exporting ? 'Формирование…' : 'Выгрузить в Excel'}
        </button>
      </div>

      {/* Bulk-actions */}
      {canValidate && (
        <div className="flex items-center gap-2 text-sm">
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
        </div>
      )}

      {/* Таблица */}
      <section className="rounded-lg border bg-white">
        <div className="overflow-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                {canValidate && (
                  <th className="px-2 py-2 w-8">
                    <input type="checkbox" checked={allChecked} onChange={toggleAll} />
                  </th>
                )}
                <th className="px-3 py-2 text-left">Показатель</th>
                <th className="px-3 py-2 text-left">Регион</th>
                <th className="px-3 py-2 text-left">Период</th>
                <th className="px-3 py-2 text-right">Значение</th>
                <th className="px-3 py-2 text-left">Статус</th>
                <th className="px-3 py-2 text-left">Утв.</th>
                <th className="px-3 py-2 text-left">Замечания проверки</th>
                {canAnnotate && <th className="px-3 py-2 text-left">Комментарии экспертов</th>}
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={9} className="px-3 py-8 text-center text-gray-500">
                    Загрузка…
                  </td>
                </tr>
              )}
              {!loading && data?.items.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-3 py-8 text-center text-gray-500">
                    Нет значений под фильтр
                  </td>
                </tr>
              )}
              {!loading &&
                data?.items.map((v) => (
                  <tr key={v.id} className="border-t hover:bg-gray-50">
                    {canValidate && (
                      <td className="px-2 py-1.5">
                        <input
                          type="checkbox"
                          checked={selected.has(v.id)}
                          onChange={() => toggleOne(v.id)}
                        />
                      </td>
                    )}
                    <td className="px-3 py-1.5">{v.indicator_name}</td>
                    <td className="px-3 py-1.5">{v.region_name}</td>
                    <td className="px-3 py-1.5 whitespace-nowrap">
                      {v.period_type} {v.period_start.slice(0, 10)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">{v.value}</td>
                    <td className="px-3 py-1.5">
                      <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_BADGE[v.validation_status] ?? ''}`}>
                        {v.validation_status}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-xs text-gray-500">
                      {v.validated_at ? new Date(v.validated_at).toLocaleString('ru-RU') : '—'}
                    </td>
                    <td className="px-3 py-1.5 text-xs">
                      {v.quality_flags?.length ? (
                        <div className="flex flex-col gap-0.5">
                          {v.quality_flags.map((f, i) => (
                            <a
                              key={i}
                              href={`/dashboard/data-intake/jobs/${f.job_id}`}
                              title={`Партия ${f.source_ref}`}
                              className={`w-fit rounded px-1.5 py-0.5 hover:underline ${FLAG_STYLE[f.severity] ?? ''}`}
                            >
                              {f.message}
                            </a>
                          ))}
                        </div>
                      ) : (
                        <span className="text-gray-300">—</span>
                      )}
                    </td>
                    {canAnnotate && (
                      <td className="px-3 py-1.5 text-xs">
                        {(notes[v.id] ?? []).map((a) => (
                          <div key={a.id} className="mb-0.5 rounded bg-amber-50 px-1.5 py-0.5 text-amber-900" title={a.created_at ? new Date(a.created_at).toLocaleString('ru-RU') : ''}>
                            <span className="font-medium">{a.author_name ?? 'эксперт'}:</span> {a.comment}
                          </div>
                        ))}
                        <button
                          onClick={() => { setNoteFor(v); setNoteText('') }}
                          className="text-primary-600 hover:underline"
                        >
                          + отметить
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
            </tbody>
          </table>
        </div>

        {/* Пагинация */}
        {data && data.pages > 1 && (
          <div className="flex items-center justify-between px-4 py-2 border-t text-sm">
            <span className="text-gray-500">
              {data.total} всего · стр. {data.page} из {data.pages}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="rounded border px-3 py-1 text-xs disabled:opacity-50"
              >
                ←
              </button>
              <button
                onClick={() => setPage((p) => Math.min(data.pages, p + 1))}
                disabled={page === data.pages}
                className="rounded border px-3 py-1 text-xs disabled:opacity-50"
              >
                →
              </button>
            </div>
          </div>
        )}
      </section>

      {noteFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
            <h3 className="mb-1 font-semibold text-gray-900">Комментарий к значению</h3>
            <p className="mb-3 text-xs text-gray-500">
              {noteFor.indicator_name} · {noteFor.region_name} · значение {noteFor.value}
            </p>
            <textarea
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
              rows={4}
              maxLength={4000}
              placeholder="Например: значение выбивается из ряда, требует проверки"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none"
            />
            <div className="mt-3 flex justify-end gap-2">
              <button onClick={() => setNoteFor(null)} className="rounded-lg border px-3 py-1.5 text-sm">Отмена</button>
              <button
                onClick={saveNote}
                disabled={!noteText.trim() || noteSaving}
                className="rounded-lg bg-primary-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              >
                {noteSaving ? 'Сохранение…' : 'Сохранить'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
