'use client'

import { useState, useEffect, useCallback } from 'react'
import { useAuthStore } from '@/store/authStore'
import {
  fetchSpatialRecords,
  uploadSpatialData,
  validateSpatialData,
  fetchDataLayers,
  fetchRegions,
  SpatialRecord,
} from '@/lib/api/backend'
import type { DataLayer, Region } from '@/lib/api/backend'

type StatusFilter = 'all' | 'pending' | 'approved' | 'rejected'

const STATUS_LABELS: Record<string, { label: string; cls: string }> = {
  pending:  { label: 'На проверке', cls: 'bg-yellow-100 text-yellow-700' },
  approved: { label: 'Утверждён',   cls: 'bg-green-100 text-green-700' },
  rejected: { label: 'Отклонён',    cls: 'bg-red-100 text-red-700' },
}

export default function SpatialDataPage() {
  const { hasRole } = useAuthStore()
  const canUpload   = hasRole('exp_ver') || hasRole('exp_dir') || hasRole('admin')
  const canValidate = hasRole('exp_ver') || hasRole('admin')

  const [records, setRecords]     = useState<SpatialRecord[]>([])
  const [total, setTotal]         = useState(0)
  const [loading, setLoading]     = useState(true)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')

  // Upload modal
  const [uploadOpen, setUploadOpen] = useState(false)
  const [layers, setLayers]     = useState<DataLayer[]>([])
  const [regions, setRegions]   = useState<Region[]>([])
  const [layerId, setLayerId]   = useState('')
  const [regionId, setRegionId] = useState('')
  const [dataDate, setDataDate] = useState(new Date().toISOString().slice(0, 10))
  const [geojsonText, setGeojsonText] = useState('')
  const [geojsonError, setGeojsonError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')

  // Validate modal
  const [validateRecord, setValidateRecord] = useState<SpatialRecord | null>(null)
  const [validateStatus, setValidateStatus] = useState<'approved' | 'rejected'>('approved')
  const [validateComment, setValidateComment] = useState('')
  const [validating, setValidating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = statusFilter !== 'all' ? { validation_status: statusFilter as 'pending' | 'approved' | 'rejected' } : {}
      const res = await fetchSpatialRecords({ ...params, page_size: 100 })
      setRecords(res.items)
      setTotal(res.total)
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!uploadOpen) return
    fetchDataLayers().then(setLayers).catch(() => {})
    fetchRegions().then((r) => setRegions(r.filter((x) => x.level === 2))).catch(() => {})
  }, [uploadOpen])

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      setGeojsonText(ev.target?.result as string ?? '')
      setGeojsonError('')
    }
    reader.readAsText(file)
  }

  async function handleUpload(e: React.FormEvent) {
    e.preventDefault()
    setUploadError('')
    setGeojsonError('')

    let geojson: object | undefined
    if (geojsonText.trim()) {
      try {
        geojson = JSON.parse(geojsonText)
      } catch {
        setGeojsonError('Некорректный JSON. Проверьте формат.')
        return
      }
    }

    setUploading(true)
    try {
      await uploadSpatialData({
        layer_id: layerId,
        region_id: regionId || undefined,
        data_date: dataDate,
        geojson,
      })
      setUploadOpen(false)
      setGeojsonText('')
      setLayerId('')
      setRegionId('')
      load()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Ошибка загрузки'
      setUploadError(msg)
    } finally {
      setUploading(false)
    }
  }

  async function handleValidate() {
    if (!validateRecord) return
    setValidating(true)
    try {
      await validateSpatialData(validateRecord.id, validateStatus, validateComment || undefined)
      setValidateRecord(null)
      setValidateComment('')
      load()
    } catch {
      // ignore
    } finally {
      setValidating(false)
    }
  }

  const pendingCount = records.filter((r) => r.validation_status === 'pending').length

  return (
    <>
      {/* Header */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Управление данными</h1>
          <p className="mt-0.5 text-sm text-gray-500">Загрузка и валидация пространственных данных СКО</p>
        </div>
        {canUpload && (
          <button
            onClick={() => setUploadOpen(true)}
            className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
          >
            + Загрузить слой
          </button>
        )}
      </div>

      {/* Status counters */}
      {canValidate && pendingCount > 0 && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-yellow-200 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          <span className="font-semibold">{pendingCount}</span> записей ожидают валидации
          <button
            onClick={() => setStatusFilter('pending')}
            className="ml-2 underline text-yellow-700 hover:text-yellow-900"
          >
            Показать
          </button>
        </div>
      )}

      {/* Status filter */}
      <div className="mb-4 flex gap-2">
        {(['all', 'pending', 'approved', 'rejected'] as StatusFilter[]).map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`rounded-full px-3 py-1 text-sm font-medium transition-colors ${
              statusFilter === s
                ? 'bg-primary-600 text-white'
                : 'bg-white text-gray-600 ring-1 ring-gray-200 hover:bg-gray-50'
            }`}
          >
            {s === 'all' ? 'Все' : STATUS_LABELS[s].label}
          </button>
        ))}
        <span className="ml-auto self-center text-sm text-gray-400">{total} записей</span>
      </div>

      {/* Records table */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-5 py-3 text-left text-xs font-medium text-gray-500">Слой</th>
              <th className="px-5 py-3 text-left text-xs font-medium text-gray-500">Район</th>
              <th className="px-5 py-3 text-left text-xs font-medium text-gray-500">Дата данных</th>
              <th className="px-5 py-3 text-left text-xs font-medium text-gray-500">Статус</th>
              {canValidate && <th className="px-5 py-3 text-left text-xs font-medium text-gray-500">Действия</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i} className="animate-pulse">
                  {Array.from({ length: canValidate ? 5 : 4 }).map((_, j) => (
                    <td key={j} className="px-5 py-3">
                      <div className="h-4 rounded bg-gray-200" />
                    </td>
                  ))}
                </tr>
              ))
            ) : records.length === 0 ? (
              <tr>
                <td colSpan={canValidate ? 5 : 4} className="py-16 text-center text-gray-400">
                  Нет записей
                </td>
              </tr>
            ) : (
              records.map((rec) => {
                const st = STATUS_LABELS[rec.validation_status] ?? { label: rec.validation_status, cls: '' }
                return (
                  <tr key={rec.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3 font-medium text-gray-900">{rec.layer_name ?? rec.layer_id}</td>
                    <td className="px-5 py-3 text-gray-500">{rec.region_name ?? '—'}</td>
                    <td className="px-5 py-3 text-gray-500">{rec.data_date}</td>
                    <td className="px-5 py-3">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${st.cls}`}>
                        {st.label}
                      </span>
                    </td>
                    {canValidate && (
                      <td className="px-5 py-3">
                        {rec.validation_status === 'pending' ? (
                          <button
                            onClick={() => { setValidateRecord(rec); setValidateStatus('approved'); setValidateComment('') }}
                            className="rounded-md bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:bg-primary-100"
                          >
                            Проверить
                          </button>
                        ) : (
                          <span className="text-xs text-gray-400">
                            {rec.validated_by_username ?? '—'}
                          </span>
                        )}
                      </td>
                    )}
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Upload modal */}
      {uploadOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog">
          <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <h3 className="font-semibold text-gray-900">Загрузить пространственные данные</h3>
              <button onClick={() => { setUploadOpen(false); setGeojsonError(''); setUploadError('') }}
                className="text-gray-400 hover:text-gray-700 text-xl leading-none">×</button>
            </div>
            <form onSubmit={handleUpload} className="px-6 py-4 space-y-4">
              {uploadError && (
                <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">{uploadError}</div>
              )}

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Слой данных</label>
                <select required value={layerId} onChange={(e) => setLayerId(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500">
                  <option value="">— Выбрать слой —</option>
                  {layers.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Район (опционально)</label>
                  <select value={regionId} onChange={(e) => setRegionId(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500">
                    <option value="">— Вся область —</option>
                    {regions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Дата данных</label>
                  <input type="date" required value={dataDate} onChange={(e) => setDataDate(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500" />
                </div>
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">
                  Геометрия (GeoJSON)
                  <span className="ml-1 text-xs font-normal text-gray-400">— вставьте JSON или загрузите файл</span>
                </label>
                <input type="file" accept=".geojson,.json" onChange={handleFileChange}
                  className="mb-2 block w-full text-sm text-gray-500 file:mr-3 file:rounded-lg file:border-0 file:bg-gray-100 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-gray-700 hover:file:bg-gray-200" />
                <textarea
                  value={geojsonText}
                  onChange={(e) => { setGeojsonText(e.target.value); setGeojsonError('') }}
                  rows={6}
                  placeholder={'{\n  "type": "Point",\n  "coordinates": [69.14, 54.87]\n}'}
                  className={`w-full rounded-lg border px-3 py-2 font-mono text-xs focus:outline-none focus:ring-1 ${
                    geojsonError ? 'border-red-300 focus:border-red-400 focus:ring-red-300' : 'border-gray-300 focus:border-primary-500 focus:ring-primary-500'
                  }`}
                />
                {geojsonError && <p className="mt-1 text-xs text-red-600">{geojsonError}</p>}
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={() => setUploadOpen(false)}
                  className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
                  Отмена
                </button>
                <button type="submit" disabled={uploading || !layerId}
                  className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50">
                  {uploading ? 'Загружаю...' : 'Загрузить'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Validate modal */}
      {validateRecord && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog">
          <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <h3 className="font-semibold text-gray-900">Валидация записи</h3>
              <button onClick={() => setValidateRecord(null)} className="text-gray-400 hover:text-gray-700 text-xl leading-none">×</button>
            </div>
            <div className="px-6 py-4 space-y-4">
              <div className="rounded-lg bg-gray-50 px-4 py-3 text-sm">
                <div className="font-medium text-gray-800">{validateRecord.layer_name}</div>
                <div className="text-gray-500">{validateRecord.region_name ?? 'Вся область'} · {validateRecord.data_date}</div>
              </div>

              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => setValidateStatus('approved')}
                  className={`flex-1 rounded-lg border-2 py-2.5 text-sm font-medium transition-colors ${
                    validateStatus === 'approved' ? 'border-green-500 bg-green-50 text-green-700' : 'border-gray-200 text-gray-600 hover:border-gray-300'
                  }`}
                >
                  ✓ Утвердить
                </button>
                <button
                  type="button"
                  onClick={() => setValidateStatus('rejected')}
                  className={`flex-1 rounded-lg border-2 py-2.5 text-sm font-medium transition-colors ${
                    validateStatus === 'rejected' ? 'border-red-500 bg-red-50 text-red-700' : 'border-gray-200 text-gray-600 hover:border-gray-300'
                  }`}
                >
                  ✗ Отклонить
                </button>
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">
                  Комментарий{validateStatus === 'rejected' && <span className="text-red-500 ml-1">*</span>}
                </label>
                <textarea
                  value={validateComment}
                  onChange={(e) => setValidateComment(e.target.value)}
                  rows={3}
                  placeholder={validateStatus === 'rejected' ? 'Укажите причину отклонения...' : 'Опционально...'}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>
            </div>
            <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
              <button onClick={() => setValidateRecord(null)}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
                Отмена
              </button>
              <button
                onClick={handleValidate}
                disabled={validating || (validateStatus === 'rejected' && !validateComment.trim())}
                className={`rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50 ${
                  validateStatus === 'approved' ? 'bg-green-600 hover:bg-green-700' : 'bg-red-600 hover:bg-red-700'
                }`}
              >
                {validating ? 'Сохраняю...' : validateStatus === 'approved' ? 'Утвердить' : 'Отклонить'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
