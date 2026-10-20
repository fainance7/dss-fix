'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import api from '@/lib/api'
import { useAuthStore } from '@/store/authStore'
import QualityReport, { type QualityReportData } from '@/components/data-intake/QualityReport'

type IntakeRow = {
  id: string
  row_no: number
  status: 'ok' | 'invalid' | 'imported' | 'skipped'
  indicator_id?: string | null
  indicator_name?: string | null
  region_id?: string | null
  region_name?: string | null
  period_type?: string | null
  period_start?: string | null
  period_end?: string | null
  value?: number | null
  error_message?: string | null
  raw_payload: Record<string, unknown>
}

type IntakeJobDetail = {
  id: string
  source_kind: 'excel_template' | 'remote_reports_db'
  source_ref: string
  uploaded_at: string
  uploaded_by?: string | null
  status: 'pending' | 'validated' | 'imported' | 'failed' | 'cancelled'
  rows_total: number
  rows_ok: number
  rows_invalid: number
  rows_imported: number
  validation_summary?: { top_errors?: Array<[string, number]> } | null
  rows: IntakeRow[]
  value_stats?: { pending: number; approved: number; rejected: number; missing: number }
}

type IntakePreviewItem = {
  row_no: number
  indicator_name?: string | null
  region_name?: string | null
  period_type?: string | null
  period_start?: string | null
  change_type: 'new' | 'changed' | 'unchanged'
  old_value?: number | null
  new_value?: number | null
  resets_approved: boolean
}

type IntakePreview = {
  job_id: string
  total: number
  new_count: number
  changed_count: number
  unchanged_count: number
  resets_approved_count: number
  items: IntakePreviewItem[]
}

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-700',
  validated: 'bg-blue-100 text-blue-700',
  imported: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  cancelled: 'bg-gray-100 text-gray-600',
  ok: 'bg-green-100 text-green-700',
  invalid: 'bg-red-100 text-red-700',
  skipped: 'bg-gray-100 text-gray-600',
}

type RowFilter = 'all' | 'ok' | 'invalid' | 'imported'

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const { hasRole } = useAuthStore()
  const canCommit = hasRole('admin') || hasRole('exp_ver')
  const canRevalidate = canCommit || hasRole('data_uploader')

  const [job, setJob] = useState<IntakeJobDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<RowFilter>('all')
  const [committing, setCommitting] = useState(false)
  const [approving, setApproving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [revalidating, setRevalidating] = useState(false)
  const [preview, setPreview] = useState<IntakePreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [showChanges, setShowChanges] = useState(false)
  const [quality, setQuality] = useState<QualityReportData | null>(null)
  const [qualityLoading, setQualityLoading] = useState(false)
  const [ackOpen, setAckOpen] = useState(false)
  const [ackChecked, setAckChecked] = useState(false)
  const [ackComment, setAckComment] = useState('')

  const loadQuality = useCallback(
    async (force = false) => {
      setQualityLoading(true)
      try {
        const { data } = await api.get<QualityReportData>(
          `/data-intake/jobs/${id}/quality${force ? '?refresh=true' : ''}`,
        )
        setQuality(data)
      } catch {
        setQuality(null)
      } finally {
        setQualityLoading(false)
      }
    },
    [id],
  )

  useEffect(() => {
    loadQuality()
  }, [loadQuality])

  const refresh = useCallback(async () => {
    setError(null)
    try {
      const { data } = await api.get<IntakeJobDetail>(`/data-intake/jobs/${id}`)
      setJob(data)
    } catch (e: unknown) {
      const err = e as { response?: { status?: number; data?: { detail?: string } } }
      if (err.response?.status === 403) {
        setError('Нет доступа к этой партии')
      } else if (err.response?.status === 404) {
        setError('Партия не найдена')
      } else {
        setError(err.response?.data?.detail ?? 'Не удалось загрузить партию')
      }
    }
  }, [id])

  useEffect(() => {
    refresh()
  }, [refresh])

  const filtered = useMemo(() => {
    if (!job) return []
    if (filter === 'all') return job.rows
    return job.rows.filter((r) => r.status === filter)
  }, [job, filter])

  const handleApproveJob = async (status: 'approved' | 'rejected') => {
    if (!job) return
    if (!confirm(`Установить validation_status="${status}" для всех ${job.rows_imported} значений партии?`)) return
    setApproving(true)
    try {
      const { data } = await api.post<{ updated: number; not_found: number }>(
        '/indicator-values/validate-bulk',
        { job_id: job.id, status, comment: `${status} via job UI` },
      )
      alert(`Обновлено: ${data.updated}` + (data.not_found ? `\nне найдено: ${data.not_found}` : ''))
      await refresh()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      alert(err.response?.data?.detail ?? 'Ошибка валидации')
    } finally {
      setApproving(false)
    }
  }

  const handleDelete = async () => {
    if (!job) return
    if (
      !confirm(
        `Удалить партию «${job.source_ref}»?\n\n` +
          'Будут удалены сам job и все его dataset_intake_rows. ' +
          'Записи в indicator_values не затрагиваются.',
      )
    )
      return
    setDeleting(true)
    try {
      await api.delete(`/data-intake/jobs/${job.id}`)
      router.push('/dashboard/data-intake')
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      alert(err.response?.data?.detail ?? 'Не удалось удалить партию')
      setDeleting(false)
    }
  }

  const handleRevalidate = async () => {
    if (!job) return
    setRevalidating(true)
    try {
      const { data } = await api.post<{
        rechecked: number
        became_ok: number
        still_invalid: number
      }>(`/data-intake/jobs/${job.id}/revalidate`)
      alert(
        `Перепроверено invalid-строк: ${data.rechecked}\n` +
          `Стали валидными: ${data.became_ok}\n` +
          `Остались с ошибкой: ${data.still_invalid}`,
      )
      await refresh()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      alert(err.response?.data?.detail ?? 'Не удалось перепроверить партию')
    } finally {
      setRevalidating(false)
    }
  }

  const loadPreview = async () => {
    if (!job) return
    setPreviewLoading(true)
    try {
      const { data } = await api.get<IntakePreview>(`/data-intake/jobs/${job.id}/preview`)
      setPreview(data)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      alert(err.response?.data?.detail ?? 'Не удалось построить отчёт об изменениях')
    } finally {
      setPreviewLoading(false)
    }
  }

  const blockers = quality?.summary?.blocker ?? 0

  const handleCommit = async (ack?: { comment: string }) => {
    if (!job) return
    // Блокеры контроля качества: импорт только с подтверждением и комментарием эксперта.
    if (blockers > 0 && !ack) {
      setAckChecked(false)
      setAckComment('')
      setAckOpen(true)
      return
    }
    if (!ack && !confirm('Импортировать все валидные строки в indicator_values?')) return
    setCommitting(true)
    try {
      const { data } = await api.post<{
        inserted_or_updated: number
        skipped: number
        errors: string[]
      }>(`/data-intake/jobs/${job.id}/commit`, ack ? { acknowledge: true, comment: ack.comment } : undefined)
      setAckOpen(false)
      alert(
        `Импортировано: ${data.inserted_or_updated}\nПропущено: ${data.skipped}` +
          (data.errors.length ? `\n\n${data.errors.slice(0, 5).join('\n')}` : ''),
      )
      setPreview(null)
      setShowChanges(false)
      await refresh()
      await loadQuality()
    } catch (e: unknown) {
      const err = e as { response?: { status?: number; data?: { detail?: string | { message?: string } } } }
      const detail = err.response?.data?.detail
      if (err.response?.status === 409 && typeof detail === 'object') {
        // за время просмотра появились блокеры — показать отчёт и окно подтверждения
        await loadQuality()
        setAckOpen(true)
      } else {
        alert((typeof detail === 'string' ? detail : detail?.message) ?? 'Ошибка коммита')
      }
    } finally {
      setCommitting(false)
    }
  }

  if (error) {
    return (
      <div className="p-6 space-y-3">
        <button onClick={() => router.push('/dashboard/data-intake')} className="text-sm text-primary-600 hover:underline">
          ← к импорту
        </button>
        <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700">{error}</div>
      </div>
    )
  }

  if (!job) {
    return <div className="p-6 text-sm text-gray-500">Загрузка…</div>
  }

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <button onClick={() => router.push('/dashboard/data-intake')} className="text-sm text-primary-600 hover:underline">
            ← к импорту
          </button>
          <h1 className="mt-2 text-2xl font-semibold">Партия {job.source_ref}</h1>
          <div className="mt-1 text-xs text-gray-500">
            {new Date(job.uploaded_at).toLocaleString('ru-RU')} · источник: {job.source_kind}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`rounded-full px-3 py-1 text-xs font-medium ${STATUS_BADGE[job.status] ?? ''}`}>
            {job.status}
          </span>
          {canRevalidate && job.status !== 'imported' && job.rows_invalid > 0 && (
            <button
              onClick={handleRevalidate}
              disabled={revalidating}
              className="rounded-lg border border-primary-300 bg-white px-4 py-2 text-sm font-medium text-primary-700 hover:bg-primary-50 disabled:opacity-50"
              title="Перепроверить invalid-строки после пополнения справочника показателей (без повторной загрузки файла)"
            >
              {revalidating ? 'проверка…' : 'Перепроверить'}
            </button>
          )}
          {canCommit && job.status !== 'imported' && job.rows_ok > 0 && (
            <button
              onClick={() => handleCommit()}
              disabled={committing}
              className={`rounded-lg px-4 py-2 text-sm font-medium text-white disabled:bg-gray-300 ${
                blockers > 0 ? 'bg-amber-600' : 'bg-green-600'
              }`}
              title={blockers > 0 ? 'В партии есть блокеры — потребуется подтверждение с комментарием' : undefined}
            >
              {committing ? 'импорт…' : blockers > 0 ? 'Импортировать с подтверждением' : 'Импортировать'}
            </button>
          )}
          {canCommit && job.status !== 'imported' && (
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="rounded-lg border border-red-300 bg-white px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
            >
              {deleting ? 'удаление…' : 'Удалить партию'}
            </button>
          )}
        </div>
      </div>

      {/* Сводка */}
      <div className="grid grid-cols-4 gap-3">
        <SummaryCard label="Всего строк" value={job.rows_total} />
        <SummaryCard label="Готовы к импорту" value={job.rows_ok} className="text-green-700" />
        <SummaryCard label="С ошибками" value={job.rows_invalid} className="text-red-700" />
        <SummaryCard label="Импортировано" value={job.rows_imported} className="text-blue-700" />
      </div>

      {/* Контроль качества (до импорта — живой отчёт, после — снимок) */}
      {job.rows_ok + job.rows_imported > 0 && (
        <QualityReport
          report={quality}
          loading={qualityLoading}
          onRefresh={() => loadQuality(true)}
          canRefresh={job.status !== 'imported'}
        />
      )}

      {/* Отчёт-предпросмотр изменений (до commit, опционально) */}
      {job.status !== 'imported' && job.rows_ok > 0 && (
        <section className="rounded-lg border bg-white p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">
              Отчёт об изменениях (предпросмотр импорта)
            </h2>
            <button
              type="button"
              onClick={loadPreview}
              disabled={previewLoading}
              className="rounded-lg border border-primary-300 bg-white px-3 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-50 disabled:opacity-50"
            >
              {previewLoading
                ? 'подсчёт…'
                : preview
                  ? 'Обновить отчёт'
                  : 'Показать отчёт об изменениях'}
            </button>
          </div>

          {!preview && !previewLoading && (
            <p className="text-xs text-gray-500">
              Ничего не импортируется — это предварительный расчёт: сколько записей добавится,
              сколько изменится и какое значение на какое будет заменено.
            </p>
          )}

          {preview && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
                <ValueStatChip
                  label="Новые"
                  count={preview.new_count}
                  className="bg-green-50 text-green-800"
                />
                <ValueStatChip
                  label="Изменятся"
                  count={preview.changed_count}
                  className="bg-amber-50 text-amber-800"
                />
                <ValueStatChip
                  label="Без изменений"
                  count={preview.unchanged_count}
                  className="bg-gray-50 text-gray-700"
                />
                {preview.resets_approved_count > 0 && (
                  <ValueStatChip
                    label="Перезапишут approved"
                    count={preview.resets_approved_count}
                    className="bg-red-50 text-red-800"
                  />
                )}
              </div>

              {preview.resets_approved_count > 0 && (
                <div className="rounded-lg bg-red-50 p-2.5 text-xs text-red-700">
                  ⚠ {preview.resets_approved_count} значений сейчас утверждены (approved). Импорт
                  сбросит их статус на <code className="bg-white px-1 rounded">pending</code> —
                  потребуется повторное утверждение.
                </div>
              )}

              {preview.items.length > 0 && (
                <div>
                  <button
                    type="button"
                    onClick={() => setShowChanges((v) => !v)}
                    className="text-xs text-primary-600 hover:underline"
                  >
                    {showChanges
                      ? 'скрыть детали изменений'
                      : `информация об изменениях (${preview.items.length}) →`}
                  </button>

                  {showChanges && (
                    <div className="mt-2 overflow-auto rounded-lg border max-h-[50vh]">
                      <table className="min-w-full text-xs">
                        <thead className="bg-gray-50 sticky top-0">
                          <tr>
                            <th className="px-2 py-1.5 text-left">#</th>
                            <th className="px-2 py-1.5 text-left">Тип</th>
                            <th className="px-2 py-1.5 text-left">Показатель</th>
                            <th className="px-2 py-1.5 text-left">Регион</th>
                            <th className="px-2 py-1.5 text-left">Период</th>
                            <th className="px-2 py-1.5 text-right">Было</th>
                            <th className="px-2 py-1.5 text-center">→</th>
                            <th className="px-2 py-1.5 text-right">Станет</th>
                          </tr>
                        </thead>
                        <tbody>
                          {preview.items.map((it) => (
                            <tr key={it.row_no} className="border-t hover:bg-gray-50">
                              <td className="px-2 py-1.5 font-mono text-gray-500">{it.row_no}</td>
                              <td className="px-2 py-1.5">
                                <span
                                  className={`rounded-full px-2 py-0.5 ${
                                    it.change_type === 'new'
                                      ? 'bg-green-100 text-green-700'
                                      : it.change_type === 'changed'
                                        ? 'bg-amber-100 text-amber-700'
                                        : 'bg-gray-100 text-gray-600'
                                  }`}
                                >
                                  {it.change_type === 'new'
                                    ? 'новая'
                                    : it.change_type === 'changed'
                                      ? 'изменение'
                                      : 'без изм.'}
                                </span>
                                {it.resets_approved && (
                                  <span className="ml-1 rounded-full bg-red-100 px-2 py-0.5 text-red-700">
                                    сбросит approved
                                  </span>
                                )}
                              </td>
                              <td className="px-2 py-1.5">{it.indicator_name ?? '—'}</td>
                              <td className="px-2 py-1.5">{it.region_name ?? '—'}</td>
                              <td className="px-2 py-1.5 whitespace-nowrap">
                                {it.period_type && it.period_start
                                  ? `${it.period_type} ${it.period_start.slice(0, it.period_type === 'dekad' ? 10 : 7)}`
                                  : '—'}
                              </td>
                              <td className="px-2 py-1.5 text-right font-mono text-gray-500">
                                {it.old_value ?? '—'}
                              </td>
                              <td className="px-2 py-1.5 text-center text-gray-400">→</td>
                              <td className="px-2 py-1.5 text-right font-mono font-medium">
                                {it.new_value ?? '—'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {/* Статусы валидации значений (после commit) */}
      {job.value_stats && job.rows_imported > 0 && (
        <section className="rounded-lg border bg-white p-4">
          <h2 className="text-sm font-medium mb-2">
            Состояние значений в <code className="bg-gray-100 px-1 rounded text-xs">indicator_values</code>
          </h2>
          <div className="grid grid-cols-4 gap-3 text-sm">
            <ValueStatChip
              label="pending"
              count={job.value_stats.pending}
              className="bg-yellow-50 text-yellow-800"
            />
            <ValueStatChip
              label="approved"
              count={job.value_stats.approved}
              className="bg-green-50 text-green-800"
            />
            <ValueStatChip
              label="rejected"
              count={job.value_stats.rejected}
              className="bg-red-50 text-red-800"
            />
            <ValueStatChip
              label="перезаписаны новой партией"
              count={job.value_stats.missing}
              className="bg-gray-50 text-gray-700"
            />
          </div>
        </section>
      )}

      {/* Топ-ошибки */}
      {job.validation_summary?.top_errors && job.validation_summary.top_errors.length > 0 && (
        <section className="rounded-lg border bg-white p-4">
          <h2 className="text-sm font-medium mb-2">Часто встречающиеся ошибки</h2>
          <ul className="space-y-1 text-sm">
            {job.validation_summary.top_errors.map(([msg, count], i) => (
              <li key={i} className="flex justify-between">
                <span className="text-red-700">{msg}</span>
                <span className="text-gray-500 font-mono text-xs">×{count}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Фильтр + таблица строк */}
      <section className="rounded-lg border bg-white p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-gray-500">Показать:</span>
          {(['all', 'ok', 'invalid', 'imported'] as RowFilter[]).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`rounded-lg px-3 py-1 text-xs ${
                filter === f ? 'bg-primary-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              {f === 'all' ? 'все' : f}
            </button>
          ))}
          <span className="ml-auto text-xs text-gray-500">{filtered.length} строк</span>
        </div>

        <div className="overflow-auto rounded-lg border max-h-[60vh]">
          <table className="min-w-full text-xs">
            <thead className="bg-gray-50 sticky top-0">
              <tr>
                <th className="px-2 py-1.5 text-left">#</th>
                <th className="px-2 py-1.5 text-left">Статус</th>
                <th className="px-2 py-1.5 text-left">Показатель</th>
                <th className="px-2 py-1.5 text-left">Регион</th>
                <th className="px-2 py-1.5 text-left">Период</th>
                <th className="px-2 py-1.5 text-right">Значение</th>
                <th className="px-2 py-1.5 text-left">Ошибка</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-2 py-6 text-center text-gray-500">
                    Нет строк под фильтр
                  </td>
                </tr>
              )}
              {filtered.map((r) => (
                <tr key={r.id} className="border-t hover:bg-gray-50">
                  <td className="px-2 py-1.5 font-mono text-gray-500">{r.row_no}</td>
                  <td className="px-2 py-1.5">
                    <span className={`rounded-full px-2 py-0.5 ${STATUS_BADGE[r.status] ?? ''}`}>
                      {r.status}
                    </span>
                  </td>
                  <td className="px-2 py-1.5">{r.indicator_name ?? '—'}</td>
                  <td className="px-2 py-1.5">{r.region_name ?? '—'}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    {r.period_type && r.period_start
                      ? `${r.period_type} ${r.period_start.slice(0, r.period_type === 'dekad' ? 10 : 7)}`
                      : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono">{r.value ?? '—'}</td>
                  <td className="px-2 py-1.5 text-red-700 max-w-[260px] truncate" title={r.error_message ?? ''}>
                    {r.error_message ?? ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {ackOpen && (
        <AckDialog
          blockers={blockers}
          checked={ackChecked}
          comment={ackComment}
          busy={committing}
          onChecked={setAckChecked}
          onComment={setAckComment}
          onCancel={() => setAckOpen(false)}
          onConfirm={() => handleCommit({ comment: ackComment.trim() })}
        />
      )}

      {job.status === 'imported' && (
        <div className="rounded-lg bg-green-50 p-4 text-sm text-green-800 space-y-2">
          <div>
            ✓ Партия импортирована: {job.rows_imported} значений в{' '}
            <code className="bg-white px-1.5 py-0.5 rounded text-xs">indicator_values</code>{' '}
            со статусом <code className="bg-white px-1.5 py-0.5 rounded text-xs">pending</code>.
          </div>
          {canCommit && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <span>Утверждение для обучения моделей и публичных дашбордов:</span>
              <button
                onClick={() => handleApproveJob('approved')}
                disabled={approving}
                className="rounded bg-green-700 px-3 py-1 text-xs text-white disabled:opacity-50"
              >
                {approving ? '…' : 'Утвердить всю партию'}
              </button>
              <button
                onClick={() => handleApproveJob('rejected')}
                disabled={approving}
                className="rounded bg-red-700 px-3 py-1 text-xs text-white disabled:opacity-50"
              >
                Отклонить всю партию
              </button>
              <a
                href="/dashboard/indicator-values"
                className="ml-auto text-primary-700 hover:underline"
              >
                открыть в «Импортированных значениях» →
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function plural(n: number, one: string, few: string, many: string) {
  if (n % 10 === 1 && n % 100 !== 11) return one
  if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14)) return few
  return many
}

function AckDialog({
  blockers,
  checked,
  comment,
  busy,
  onChecked,
  onComment,
  onCancel,
  onConfirm,
}: {
  blockers: number
  checked: boolean
  comment: string
  busy: boolean
  onChecked: (v: boolean) => void
  onComment: (v: string) => void
  onCancel: () => void
  onConfirm: () => void
}) {
  const valid = checked && comment.trim().length >= 10
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-lg rounded-xl bg-white shadow-2xl">
        <div className="border-b border-gray-100 px-6 py-4">
          <h3 className="font-semibold text-gray-900">Импорт партии с замечаниями</h3>
        </div>
        <div className="space-y-4 p-6">
          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            В партии {blockers} {plural(blockers, 'замечание', 'замечания', 'замечаний')} уровня «блокер» (см. «Контроль качества данных»).
            Импорт возможен, но решение и его обоснование будут записаны в журнал аудита.
          </div>
          <label className="flex items-start gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={checked} onChange={(e) => onChecked(e.target.checked)} className="mt-0.5" />
            Я ознакомился с замечаниями и подтверждаю импорт
          </label>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Обоснование решения</label>
            <textarea
              value={comment}
              onChange={(e) => onComment(e.target.value)}
              rows={3}
              placeholder="Например: старые значения были в центнерах, новые в тоннах верные — согласовано с источником"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
            <p className="mt-1 text-xs text-gray-400">Не короче 10 символов</p>
          </div>
        </div>
        <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
          <button onClick={onCancel} className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
            Отмена
          </button>
          <button
            onClick={onConfirm}
            disabled={!valid || busy}
            className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? 'импорт…' : 'Импортировать'}
          </button>
        </div>
      </div>
    </div>
  )
}

function SummaryCard({
  label,
  value,
  className = '',
}: {
  label: string
  value: number | string
  className?: string
}) {
  return (
    <div className="rounded-lg border bg-white px-4 py-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${className}`}>{value}</div>
    </div>
  )
}

function ValueStatChip({
  label,
  count,
  className = '',
}: {
  label: string
  count: number
  className?: string
}) {
  return (
    <div className={`rounded-lg px-3 py-2 ${className}`}>
      <div className="text-xs">{label}</div>
      <div className="mt-0.5 text-lg font-semibold">{count}</div>
    </div>
  )
}
