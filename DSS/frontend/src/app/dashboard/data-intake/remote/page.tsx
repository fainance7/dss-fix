'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import api from '@/lib/api'
import { useAuthStore } from '@/store/authStore'

type RemoteTable = {
  table_name: string
  row_estimate: number
  column_count: number
}

type RemoteColumn = {
  index: number
  column_name: string
  data_type: string
}

type RemotePreview = {
  columns: RemoteColumn[]
  rows: unknown[][]
}

type Indicator = {
  id: string
  name: string
  direction_id: string
  direction_name?: string | null
  unit_id: string
  unit_name?: string | null
}

type Kato = {
  kato_code: string
  name_ru: string
  level: number
}

type PeriodColumn = { index: number; marker: string }

type IntakeMapping = {
  id: string
  remote_table: string
  indicator_id: string
  indicator_name?: string | null
  region_resolution_kind: 'constant' | 'by_name'
  kato_constant?: string | null
  region_column_idx?: number | null
  region_name_to_kato?: Record<string, string> | null
  period_kind: 'yearly' | 'quarterly' | 'monthly' | 'daily'
  year_column_idx?: number | null
  period_columns_idx: PeriodColumn[]
  skip_if_first_col_in: string[]
  source_name?: string | null
  notes?: string | null
  last_run_at?: string | null
  last_run_status?: string | null
  last_run_summary?: Record<string, unknown> | null
}

type RunResult = {
  job_id: string
  dry_run: boolean
  rows_total: number
  rows_ok: number
  rows_invalid: number
  rows_imported: number
  sample_errors: string[]
}

const MONTH_MARKERS: PeriodColumn[] = Array.from({ length: 12 }, (_, i) => ({
  index: i + 1,
  marker: String(i + 1).padStart(2, '0'),
}))

export default function RemoteMappingPage() {
  const { hasRole } = useAuthStore()
  const canEdit =
    hasRole('admin') || hasRole('exp_ver') || hasRole('data_uploader')

  const [tables, setTables] = useState<RemoteTable[]>([])
  const [tablesSearch, setTablesSearch] = useState('')
  const [tablesLoading, setTablesLoading] = useState(false)
  const [selectedTable, setSelectedTable] = useState<string | null>(null)

  const [preview, setPreview] = useState<RemotePreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)

  const [indicators, setIndicators] = useState<Indicator[]>([])
  const [katos, setKatos] = useState<Kato[]>([])
  const [mappings, setMappings] = useState<IntakeMapping[]>([])

  const [editing, setEditing] = useState<Partial<IntakeMapping> | null>(null)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState<'dry' | 'commit' | null>(null)
  const [runResult, setRunResult] = useState<RunResult | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  // initial load
  useEffect(() => {
    api
      .get<Indicator[]>('/indicators?is_active=true')
      .then((r) => setIndicators(Array.isArray(r.data) ? r.data : []))
      .catch(() => setIndicators([]))
    api
      .get<Kato[] | { items: Kato[] }>('/kato')
      .then((r) => {
        const data = r.data
        const items = Array.isArray(data) ? data : data.items ?? []
        setKatos(items)
      })
      .catch(() => setKatos([]))
    api
      .get<IntakeMapping[]>('/data-intake/mappings')
      .then((r) => setMappings(Array.isArray(r.data) ? r.data : []))
      .catch(() => setMappings([]))
    refreshTables('')
  }, [])

  const refreshTables = useCallback(async (search: string) => {
    setTablesLoading(true)
    try {
      const params = new URLSearchParams()
      if (search) params.set('search', search)
      params.set('limit', '300')
      const { data } = await api.get<RemoteTable[]>(`/data-intake/remote/tables?${params}`)
      setTables(data)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setErrorMsg(err.response?.data?.detail ?? 'Ошибка получения списка таблиц')
    } finally {
      setTablesLoading(false)
    }
  }, [])

  // preview when table selected
  useEffect(() => {
    if (!selectedTable) {
      setPreview(null)
      return
    }
    setPreviewLoading(true)
    api
      .get<RemotePreview>(`/data-intake/remote/tables/${encodeURIComponent(selectedTable)}/preview?limit=10`)
      .then((r) => setPreview(r.data))
      .catch((e) => {
        setPreview(null)
        const err = e as { response?: { data?: { detail?: string } } }
        setErrorMsg(err.response?.data?.detail ?? 'Не удалось загрузить preview')
      })
      .finally(() => setPreviewLoading(false))
  }, [selectedTable])

  const tableMappings = useMemo(
    () => mappings.filter((m) => m.remote_table === selectedTable),
    [mappings, selectedTable],
  )

  const startNewMapping = () => {
    if (!selectedTable) return
    setRunResult(null)
    setEditing({
      remote_table: selectedTable,
      region_resolution_kind: 'constant',
      kato_constant: '590000000',
      period_kind: 'monthly',
      year_column_idx: 0,
      period_columns_idx: MONTH_MARKERS,
      skip_if_first_col_in: ['среднее', 'Среднее', 'сумма', 'Сумма'],
    })
  }

  const editExisting = (m: IntakeMapping) => {
    setRunResult(null)
    setEditing({ ...m })
  }

  const saveMapping = async () => {
    if (!editing) return
    if (!editing.indicator_id) {
      alert('Выберите показатель')
      return
    }
    if (editing.region_resolution_kind === 'constant' && !editing.kato_constant) {
      alert('Укажите КАТО (constant)')
      return
    }
    setSaving(true)
    try {
      const payload = { ...editing }
      let saved: IntakeMapping
      if (editing.id) {
        const { data } = await api.patch<IntakeMapping>(`/data-intake/mappings/${editing.id}`, payload)
        saved = data
      } else {
        const { data } = await api.post<IntakeMapping>('/data-intake/mappings', payload)
        saved = data
      }
      const list = (await api.get<IntakeMapping[]>('/data-intake/mappings')).data
      setMappings(list)
      setEditing(saved)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      alert(err.response?.data?.detail ?? 'Не удалось сохранить mapping')
    } finally {
      setSaving(false)
    }
  }

  const deleteMapping = async (id: string) => {
    if (!confirm('Удалить этот mapping?')) return
    try {
      await api.delete(`/data-intake/mappings/${id}`)
      setMappings((m) => m.filter((x) => x.id !== id))
      if (editing?.id === id) setEditing(null)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      alert(err.response?.data?.detail ?? 'Ошибка удаления')
    }
  }

  const runMapping = async (commit: boolean) => {
    if (!editing?.id) {
      alert('Сначала сохраните mapping')
      return
    }
    if (commit && !confirm('UPSERT всех ok-строк в indicator_values. Продолжить?')) return
    setRunning(commit ? 'commit' : 'dry')
    setRunResult(null)
    try {
      const { data } = await api.post<RunResult>(
        `/data-intake/mappings/${editing.id}/run?dry_run=${!commit}`,
      )
      setRunResult(data)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      alert(err.response?.data?.detail ?? 'Ошибка запуска ETL')
    } finally {
      setRunning(null)
    }
  }

  if (!canEdit) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-semibold mb-2">Разметка remote-таблиц</h1>
        <p className="text-sm text-gray-600">
          Доступ только для admin / exp_ver / data_uploader.
        </p>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Разметка remote-таблиц reports_db</h1>
          <p className="mt-1 text-sm text-gray-600">
            Привязка таблиц первичной БД к показателям DSS без переливки файлов.
            Выберите таблицу слева, опишите её структуру, проверьте dry-run, утвердите импорт.
          </p>
        </div>
      </div>

      {errorMsg && (
        <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {errorMsg}
          <button className="ml-3 underline" onClick={() => setErrorMsg(null)}>скрыть</button>
        </div>
      )}

      <div className="grid grid-cols-12 gap-4">
        {/* LEFT — список таблиц */}
        <aside className="col-span-3 rounded-lg border bg-white p-3 space-y-2 max-h-[80vh] overflow-auto">
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="поиск..."
              value={tablesSearch}
              onChange={(e) => setTablesSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && refreshTables(tablesSearch)}
              className="w-full rounded border px-2 py-1 text-sm"
            />
            <button
              onClick={() => refreshTables(tablesSearch)}
              className="rounded bg-primary-600 px-3 py-1 text-xs text-white"
            >
              ↻
            </button>
          </div>
          <div className="text-xs text-gray-500">{tablesLoading ? 'загрузка...' : `${tables.length} таблиц`}</div>
          <ul className="divide-y text-sm">
            {tables.map((t) => (
              <li
                key={t.table_name}
                className={`cursor-pointer py-1.5 px-2 rounded ${
                  selectedTable === t.table_name ? 'bg-primary-50 font-medium' : 'hover:bg-gray-50'
                }`}
                onClick={() => setSelectedTable(t.table_name)}
                title={t.table_name}
              >
                <div className="truncate">{t.table_name}</div>
                <div className="text-[10px] text-gray-400">cols={t.column_count}</div>
              </li>
            ))}
          </ul>
        </aside>

        {/* RIGHT — preview + mappings + editor */}
        <main className="col-span-9 space-y-4">
          {!selectedTable && (
            <div className="rounded-lg border bg-white p-6 text-sm text-gray-500">
              Выберите таблицу слева для просмотра.
            </div>
          )}

          {selectedTable && (
            <>
              {/* PREVIEW */}
              <section className="rounded-lg border bg-white p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <h2 className="font-medium">Preview: <span className="font-mono text-sm">{selectedTable}</span></h2>
                  <div className="text-xs text-gray-500">{previewLoading && 'загрузка...'}</div>
                </div>
                {preview && (
                  <div className="overflow-auto max-h-72">
                    <table className="min-w-full text-xs font-mono">
                      <thead className="bg-gray-50 sticky top-0">
                        <tr>
                          {preview.columns.map((c) => (
                            <th key={c.index} className="px-2 py-1 text-left whitespace-nowrap">
                              <div className="text-[10px] text-gray-400">[{c.index}] {c.data_type}</div>
                              <div>{c.column_name}</div>
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {preview.rows.map((r, i) => (
                          <tr key={i} className="border-t">
                            {r.map((v, j) => (
                              <td key={j} className="px-2 py-1 whitespace-nowrap">
                                {v === null ? <span className="text-gray-300">null</span> : String(v)}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              {/* MAPPINGS LIST */}
              <section className="rounded-lg border bg-white p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <h2 className="font-medium">Привязки этой таблицы</h2>
                  <button
                    onClick={startNewMapping}
                    className="rounded bg-primary-600 px-3 py-1 text-xs text-white"
                  >
                    + Новая привязка
                  </button>
                </div>
                {tableMappings.length === 0 ? (
                  <div className="text-sm text-gray-500">Привязок нет.</div>
                ) : (
                  <table className="w-full text-sm">
                    <thead className="text-xs text-gray-500">
                      <tr>
                        <th className="text-left px-2 py-1">Показатель</th>
                        <th className="text-left px-2 py-1">Период</th>
                        <th className="text-left px-2 py-1">Регион</th>
                        <th className="text-left px-2 py-1">Last run</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {tableMappings.map((m) => (
                        <tr key={m.id} className="border-t">
                          <td className="px-2 py-1.5">{m.indicator_name}</td>
                          <td className="px-2 py-1.5">{m.period_kind}</td>
                          <td className="px-2 py-1.5">
                            {m.region_resolution_kind === 'constant' ? `КАТО ${m.kato_constant}` : 'by_name'}
                          </td>
                          <td className="px-2 py-1.5 text-xs text-gray-600">
                            {m.last_run_at ? `${m.last_run_status} @ ${new Date(m.last_run_at).toLocaleString('ru-RU')}` : '—'}
                          </td>
                          <td className="px-2 py-1.5 text-right">
                            <button onClick={() => editExisting(m)} className="text-primary-600 text-xs hover:underline mr-2">
                              открыть
                            </button>
                            <button onClick={() => deleteMapping(m.id)} className="text-red-600 text-xs hover:underline">
                              удалить
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>

              {/* EDITOR */}
              {editing && (
                <section className="rounded-lg border bg-white p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <h2 className="font-medium">{editing.id ? 'Редактирование' : 'Новая привязка'}</h2>
                    <button onClick={() => setEditing(null)} className="text-xs text-gray-500 hover:underline">
                      закрыть
                    </button>
                  </div>

                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <label>
                      <div className="text-xs text-gray-500 mb-0.5">Показатель *</div>
                      <select
                        className="w-full rounded border px-2 py-1"
                        value={editing.indicator_id ?? ''}
                        onChange={(e) => setEditing({ ...editing, indicator_id: e.target.value })}
                      >
                        <option value="">— выбрать —</option>
                        {indicators.map((i) => (
                          <option key={i.id} value={i.id}>
                            [{i.direction_name}] {i.name} ({i.unit_name})
                          </option>
                        ))}
                      </select>
                    </label>

                    <label>
                      <div className="text-xs text-gray-500 mb-0.5">Тип периода *</div>
                      <select
                        className="w-full rounded border px-2 py-1"
                        value={editing.period_kind ?? 'monthly'}
                        onChange={(e) =>
                          setEditing({ ...editing, period_kind: e.target.value as IntakeMapping['period_kind'] })
                        }
                      >
                        <option value="yearly">yearly</option>
                        <option value="quarterly">quarterly</option>
                        <option value="monthly">monthly</option>
                        <option value="daily">daily</option>
                      </select>
                    </label>

                    <label>
                      <div className="text-xs text-gray-500 mb-0.5">Колонка с годом (year_column_idx)</div>
                      <input
                        type="number"
                        min={0}
                        className="w-full rounded border px-2 py-1"
                        value={editing.year_column_idx ?? ''}
                        onChange={(e) =>
                          setEditing({
                            ...editing,
                            year_column_idx: e.target.value === '' ? null : Number(e.target.value),
                          })
                        }
                      />
                    </label>

                    <label>
                      <div className="text-xs text-gray-500 mb-0.5">Резолв региона</div>
                      <select
                        className="w-full rounded border px-2 py-1"
                        value={editing.region_resolution_kind ?? 'constant'}
                        onChange={(e) =>
                          setEditing({
                            ...editing,
                            region_resolution_kind: e.target.value as 'constant' | 'by_name',
                          })
                        }
                      >
                        <option value="constant">константа (один регион на всю таблицу)</option>
                        <option value="by_name">by_name (из колонки)</option>
                      </select>
                    </label>

                    {editing.region_resolution_kind === 'constant' && (
                      <label>
                        <div className="text-xs text-gray-500 mb-0.5">КАТО *</div>
                        <select
                          className="w-full rounded border px-2 py-1"
                          value={editing.kato_constant ?? ''}
                          onChange={(e) => setEditing({ ...editing, kato_constant: e.target.value })}
                        >
                          <option value="">— выбрать —</option>
                          {katos.map((k) => (
                            <option key={k.kato_code} value={k.kato_code}>
                              {k.kato_code} — {k.name_ru}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}

                    {editing.region_resolution_kind === 'by_name' && (
                      <label>
                        <div className="text-xs text-gray-500 mb-0.5">Колонка с именем региона (region_column_idx)</div>
                        <input
                          type="number"
                          min={0}
                          className="w-full rounded border px-2 py-1"
                          value={editing.region_column_idx ?? ''}
                          onChange={(e) =>
                            setEditing({
                              ...editing,
                              region_column_idx: e.target.value === '' ? null : Number(e.target.value),
                            })
                          }
                        />
                      </label>
                    )}
                  </div>

                  <div>
                    <div className="text-xs text-gray-500 mb-1">period_columns_idx (JSON массив &#123;index, marker&#125;)</div>
                    <textarea
                      className="w-full rounded border px-2 py-1 font-mono text-xs"
                      rows={4}
                      value={JSON.stringify(editing.period_columns_idx ?? [], null, 0)}
                      onChange={(e) => {
                        try {
                          const parsed = JSON.parse(e.target.value)
                          if (Array.isArray(parsed)) setEditing({ ...editing, period_columns_idx: parsed })
                        } catch {
                          /* keep typing */
                        }
                      }}
                    />
                    <button
                      type="button"
                      onClick={() => setEditing({ ...editing, period_columns_idx: MONTH_MARKERS })}
                      className="text-xs text-primary-600 hover:underline"
                    >
                      пресет: 12 месяцев (idx 1..12)
                    </button>
                  </div>

                  <div>
                    <div className="text-xs text-gray-500 mb-1">skip_if_first_col_in (через запятую)</div>
                    <input
                      className="w-full rounded border px-2 py-1 text-sm"
                      value={(editing.skip_if_first_col_in ?? []).join(', ')}
                      onChange={(e) =>
                        setEditing({
                          ...editing,
                          skip_if_first_col_in: e.target.value
                            .split(',')
                            .map((s) => s.trim())
                            .filter(Boolean),
                        })
                      }
                    />
                  </div>

                  <div className="flex gap-2">
                    <button
                      onClick={saveMapping}
                      disabled={saving}
                      className="rounded bg-primary-600 px-4 py-1.5 text-sm text-white disabled:bg-gray-300"
                    >
                      {saving ? 'сохранение…' : editing.id ? 'Сохранить' : 'Создать'}
                    </button>
                    {editing.id && (
                      <>
                        <button
                          onClick={() => runMapping(false)}
                          disabled={running !== null}
                          className="rounded bg-gray-700 px-4 py-1.5 text-sm text-white disabled:bg-gray-300"
                        >
                          {running === 'dry' ? 'dry-run…' : 'Dry-run'}
                        </button>
                        <button
                          onClick={() => runMapping(true)}
                          disabled={running !== null}
                          className="rounded bg-green-600 px-4 py-1.5 text-sm text-white disabled:bg-gray-300"
                        >
                          {running === 'commit' ? 'commit…' : 'Commit в indicator_values'}
                        </button>
                      </>
                    )}
                  </div>

                  {runResult && (
                    <div className="rounded-lg bg-gray-50 p-3 text-sm">
                      <div className="font-medium mb-1">
                        {runResult.dry_run ? 'Dry-run результат' : 'Commit результат'}
                      </div>
                      <div className="grid grid-cols-4 gap-2 text-xs">
                        <div>всего: <b>{runResult.rows_total}</b></div>
                        <div className="text-green-700">ok: <b>{runResult.rows_ok}</b></div>
                        <div className="text-red-700">invalid: <b>{runResult.rows_invalid}</b></div>
                        <div>записано: <b>{runResult.rows_imported}</b></div>
                      </div>
                      {runResult.sample_errors.length > 0 && (
                        <details className="mt-2 text-xs">
                          <summary className="cursor-pointer text-red-700">примеры ошибок</summary>
                          <ul className="mt-1 list-disc pl-5">
                            {runResult.sample_errors.map((e, i) => <li key={i}>{e}</li>)}
                          </ul>
                        </details>
                      )}
                    </div>
                  )}
                </section>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  )
}
