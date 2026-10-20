'use client'

import { useState, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import api from '@/lib/api'
import { useAuthStore } from '@/store/authStore'

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

type IntakeJob = {
  id: string
  source_kind: 'excel_template' | 'remote_reports_db'
  source_ref: string
  uploaded_at: string
  status: 'pending' | 'validated' | 'imported' | 'failed' | 'cancelled'
  rows_total: number
  rows_ok: number
  rows_invalid: number
  rows_imported: number
  quality_summary?: { blocker: number; warning: number; info: number; acknowledged: boolean } | null
}

type UploadResp = { job: IntakeJob; rows: IntakeRow[]; errors_total: number }
type JobList = { items: IntakeJob[]; total: number; page: number; page_size: number; pages: number }
type CommitResp = { job_id: string; inserted_or_updated: number; skipped: number; errors: string[] }

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

export default function DataIntakePage() {
  const { hasRole } = useAuthStore()
  const router = useRouter()
  const [file, setFile] = useState<File | null>(null)
  const [uploadResult, setUploadResult] = useState<UploadResp | null>(null)
  const [uploading, setUploading] = useState(false)
  const [jobs, setJobs] = useState<IntakeJob[]>([])
  const [committing, setCommitting] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const canCommit = hasRole('admin') || hasRole('exp_ver')
  const canUpload =
    canCommit || hasRole('exp_dir') || hasRole('data_uploader')

  const refreshJobs = useCallback(async () => {
    try {
      const { data } = await api.get<JobList>('/data-intake/jobs', {
        params: { page: 1, page_size: 50 },
      })
      setJobs(data.items)
    } catch (e: unknown) {
      console.error(e)
    }
  }, [])

  useEffect(() => {
    refreshJobs()
  }, [refreshJobs])

  const handleUpload = async () => {
    if (!file) return
    setUploading(true)
    setError(null)
    setUploadResult(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const { data } = await api.post<UploadResp>('/data-intake/excel', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setUploadResult(data)
      await refreshJobs()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setError(err.response?.data?.detail ?? 'Не удалось загрузить файл')
    } finally {
      setUploading(false)
    }
  }

  const handleDownloadTemplate = async () => {
    try {
      const resp = await api.get(`/data-intake/template?ts=${Date.now()}`, { responseType: 'blob' })
      const blob = new Blob([resp.data], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'data_intake_template.xlsx'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      alert(err.response?.data?.detail ?? 'Не удалось скачать шаблон')
    }
  }

  const handleDelete = async (jobId: string, sourceRef: string) => {
    if (
      !confirm(
        `Удалить партию «${sourceRef}»?\n\n` +
          'Будут удалены сам job и все его dataset_intake_rows. ' +
          'Записи в indicator_values не затрагиваются.',
      )
    )
      return
    setDeleting(jobId)
    try {
      await api.delete(`/data-intake/jobs/${jobId}`)
      if (uploadResult?.job.id === jobId) {
        setUploadResult(null)
      }
      await refreshJobs()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      alert(err.response?.data?.detail ?? 'Не удалось удалить партию')
    } finally {
      setDeleting(null)
    }
  }

  const handleCommit = async (jobId: string) => {
    if (!confirm('Импортировать все валидные строки в indicator_values?')) return
    setCommitting(jobId)
    try {
      const { data } = await api.post<CommitResp>(`/data-intake/jobs/${jobId}/commit`)
      alert(
        `Импортировано: ${data.inserted_or_updated}\nПропущено: ${data.skipped}` +
          (data.errors.length ? `\n\nОшибки:\n${data.errors.slice(0, 5).join('\n')}` : ''),
      )
      await refreshJobs()
      if (uploadResult?.job.id === jobId) {
        setUploadResult(null)
      }
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      alert(err.response?.data?.detail ?? 'Ошибка коммита')
    } finally {
      setCommitting(null)
    }
  }

  if (!canUpload) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-semibold mb-2">Импорт данных</h1>
        <p className="text-sm text-gray-600">
          Доступ к импорту разрешён ролям admin / exp_ver / exp_dir / data_uploader.
        </p>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Импорт исторических данных</h1>
        <p className="mt-1 text-sm text-gray-600">
          Загрузка Excel-шаблона по обязательной структуре (показатель / единица / КАТО / период / значение).
          Подробности — в{' '}
          <a href="/dashboard/data-intake/protocol" className="text-primary-600 hover:underline">
            протоколе приёма
          </a>
          .
        </p>
      </div>

      <section className="rounded-lg border bg-white p-5 space-y-4">
        <h2 className="text-lg font-medium">Новая партия</h2>
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="file"
            accept=".xlsx"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm"
          />
          <button
            type="button"
            onClick={handleUpload}
            disabled={!file || uploading}
            className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white disabled:bg-gray-300"
          >
            {uploading ? 'Загрузка…' : 'Загрузить'}
          </button>
          <button
            type="button"
            onClick={handleDownloadTemplate}
            className="text-sm text-primary-600 hover:underline"
          >
            Скачать шаблон
          </button>
        </div>
        {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
        {uploadResult && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-4 text-sm">
              <span>
                Всего строк: <b>{uploadResult.job.rows_total}</b>
              </span>
              <span className="text-green-700">
                ОК: <b>{uploadResult.job.rows_ok}</b>
              </span>
              <span className="text-red-700">
                С ошибками: <b>{uploadResult.job.rows_invalid}</b>
              </span>
              <span>
                Партия:{' '}
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${STATUS_BADGE[uploadResult.job.status] ?? ''}`}
                >
                  {uploadResult.job.status}
                </span>
              </span>
              <button
                type="button"
                onClick={() => router.push(`/dashboard/data-intake/jobs/${uploadResult.job.id}`)}
                className="ml-auto text-sm text-primary-600 hover:underline"
              >
                открыть партию →
              </button>
            </div>
            {uploadResult.errors_total > 0 && (
              <div className="overflow-auto rounded-lg border">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-3 py-2 text-left">Строка</th>
                      <th className="px-3 py-2 text-left">Ошибка</th>
                      <th className="px-3 py-2 text-left">Сырые данные</th>
                    </tr>
                  </thead>
                  <tbody>
                    {uploadResult.rows
                      .filter((r) => r.status === 'invalid')
                      .slice(0, 30)
                      .map((r) => (
                        <tr key={r.id} className="border-t">
                          <td className="px-3 py-2">{r.row_no}</td>
                          <td className="px-3 py-2 text-red-700">{r.error_message}</td>
                          <td className="px-3 py-2 text-xs text-gray-600">
                            {JSON.stringify(r.raw_payload)}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
            {uploadResult.errors_total === 0 && canCommit && (
              <button
                type="button"
                onClick={() => handleCommit(uploadResult.job.id)}
                disabled={committing === uploadResult.job.id}
                className="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white disabled:bg-gray-300"
              >
                {committing === uploadResult.job.id ? 'Импорт…' : 'Импортировать в indicator_values'}
              </button>
            )}
          </div>
        )}
      </section>

      <section className="rounded-lg border bg-white p-5 space-y-3">
        <h2 className="text-lg font-medium">История партий</h2>
        <div className="overflow-auto rounded-lg border">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left">Дата</th>
                <th className="px-3 py-2 text-left">Источник</th>
                <th className="px-3 py-2 text-left">Файл / таблица</th>
                <th className="px-3 py-2 text-left">Статус</th>
                <th className="px-3 py-2 text-left">Качество</th>
                <th className="px-3 py-2 text-right">Всего</th>
                <th className="px-3 py-2 text-right">ОК</th>
                <th className="px-3 py-2 text-right">Ошибок</th>
                <th className="px-3 py-2 text-right">Импорт</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-3 py-8 text-center text-gray-500">
                    Нет партий
                  </td>
                </tr>
              ) : (
                jobs.map((j) => (
                  <tr
                    key={j.id}
                    className="border-t cursor-pointer hover:bg-gray-50"
                    onClick={() => router.push(`/dashboard/data-intake/jobs/${j.id}`)}
                  >
                    <td className="px-3 py-2">{new Date(j.uploaded_at).toLocaleString('ru-RU')}</td>
                    <td className="px-3 py-2">{j.source_kind}</td>
                    <td className="px-3 py-2 max-w-[260px] truncate">{j.source_ref}</td>
                    <td className="px-3 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_BADGE[j.status] ?? ''}`}>
                        {j.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap text-xs">
                      <QualityBadge q={j.quality_summary} />
                    </td>
                    <td className="px-3 py-2 text-right">{j.rows_total}</td>
                    <td className="px-3 py-2 text-right text-green-700">{j.rows_ok}</td>
                    <td className="px-3 py-2 text-right text-red-700">{j.rows_invalid}</td>
                    <td className="px-3 py-2 text-right">{j.rows_imported}</td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex items-center justify-end gap-3">
                        {canCommit && j.status !== 'imported' && j.rows_ok > 0 && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              // с блокерами импорт подтверждается на странице партии
                              if (j.quality_summary?.blocker) router.push(`/dashboard/data-intake/jobs/${j.id}`)
                              else handleCommit(j.id)
                            }}
                            disabled={committing === j.id}
                            className="text-xs text-primary-600 hover:underline disabled:opacity-50"
                          >
                            {committing === j.id ? '…' : 'импорт'}
                          </button>
                        )}
                        {canCommit && j.status !== 'imported' && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              handleDelete(j.id, j.source_ref)
                            }}
                            disabled={deleting === j.id}
                            className="text-xs text-red-600 hover:underline disabled:opacity-50"
                          >
                            {deleting === j.id ? '…' : 'удалить'}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

function QualityBadge({ q }: { q?: IntakeJob['quality_summary'] }) {
  if (!q) return <span className="text-gray-400">—</span>
  if (!q.blocker && !q.warning) return <span className="text-green-700" title="Замечаний нет">✓ нет замечаний</span>
  return (
    <span className="inline-flex gap-1">
      {q.blocker > 0 && (
        <span className="rounded-full bg-red-100 px-2 py-0.5 text-red-700" title="Замечания уровня «блокер»">
          блокер {q.blocker}
        </span>
      )}
      {q.warning > 0 && (
        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800" title="Предупреждения">
          ⚠ {q.warning}
        </span>
      )}
      {q.acknowledged && <span className="text-gray-500" title="Импорт подтверждён экспертом">✓ подтв.</span>}
    </span>
  )
}
