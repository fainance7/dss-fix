'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useAuthStore } from '@/store/authStore'
import KatoMappingForm from '@/components/gis/KatoMappingForm'
import {
  gisListCategories,
  gisCreateDataset,
  gisGetDataset,
  gisGetDatasetStatus,
  gisRasterIngest,
  gisRasterIngestStatus,
  type GisCategory,
  type GisDatasetDetail,
  type GisFileType,
  type GisRasterIngestStatus,
} from '@/lib/api/backend'

const FILE_TYPE_LABEL: Record<GisFileType, string> = {
  vector: 'Вектор без KATO',
  vector_with_kato: 'Вектор с KATO (районные показатели)',
  raster: 'Растр (GeoTIFF)',
}

const ACCEPT_BY_TYPE: Record<GisFileType, string> = {
  vector: '.zip,.geojson,.gpkg',
  vector_with_kato: '.zip,.geojson,.gpkg',
  raster: '.tif,.tiff',
}

function errMessage(e: unknown, fallback: string): string {
  const err = e as { response?: { data?: { detail?: string } } }
  return err.response?.data?.detail ?? fallback
}

function StatusBadge({ status }: { status?: string | null }) {
  if (!status) return null
  const cls =
    status === 'ready'
      ? 'bg-green-100 text-green-700'
      : status === 'failed'
        ? 'bg-red-100 text-red-700'
        : 'bg-blue-100 text-blue-700'
  return <span className={`rounded-full px-2 py-0.5 text-xs ${cls}`}>{status}</span>
}

export default function GisUploadPage() {
  const { hasRole } = useAuthStore()
  const canUpload =
    hasRole('admin') || hasRole('exp_ver') || hasRole('exp_dir') || hasRole('data_uploader')

  const [categories, setCategories] = useState<GisCategory[]>([])
  const [refError, setRefError] = useState<string | null>(null)

  // Метаданные слоя
  const [categoryId, setCategoryId] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [unit, setUnit] = useState('')
  const [fileType, setFileType] = useState<GisFileType>('vector_with_kato')
  const [file, setFile] = useState<File | null>(null)

  const [creating, setCreating] = useState(false)
  const [uploadPct, setUploadPct] = useState<number | null>(null) // процент байтового аплоада
  const [dataset, setDataset] = useState<GisDatasetDetail | null>(null)

  // KATO-маппинг (для vector_with_kato)
  const [mappingDone, setMappingDone] = useState(false)
  const mappingDoneRef = useRef(false)

  // Статус обработки на стороне GIS-сервиса
  const [status, setStatus] = useState<string | null>(null)
  const [parseError, setParseError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Инжест COG растра в тайл-сервис (titiler)
  const [ingest, setIngest] = useState<GisRasterIngestStatus | null>(null)
  const ingestPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const [error, setError] = useState<string | null>(null)

  const loadCats = useCallback(async () => {
    setRefError(null)
    try {
      setCategories(await gisListCategories())
    } catch (e) {
      setRefError(errMessage(e, 'Не удалось загрузить категории'))
    }
  }, [])

  useEffect(() => {
    if (canUpload) loadCats()
  }, [canUpload, loadCats])

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
      if (ingestPollRef.current) clearInterval(ingestPollRef.current)
    }
  }, [])

  // Инжест COG растра: запрашиваем скачивание и опрашиваем прогресс.
  const startIngest = useCallback(async (id: string) => {
    if (ingestPollRef.current) clearInterval(ingestPollRef.current)
    setIngest({ status: 'running', downloaded: 0, total: null })
    try {
      const first = await gisRasterIngest(id)
      setIngest(first)
      if (first.status !== 'running') return
    } catch (e) {
      setIngest({ status: 'error', error: errMessage(e, 'Ошибка инжеста растра') })
      return
    }
    ingestPollRef.current = setInterval(async () => {
      try {
        const s = await gisRasterIngestStatus(id)
        setIngest(s)
        if (s.status === 'done' || s.status === 'error') {
          if (ingestPollRef.current) clearInterval(ingestPollRef.current)
        }
      } catch (e) {
        if (ingestPollRef.current) clearInterval(ingestPollRef.current)
        setIngest({ status: 'error', error: errMessage(e, 'Ошибка опроса инжеста') })
      }
    }, 2000)
  }, [])

  // Поллинг статуса обработки. ftype фиксируем на момент запуска.
  const startPolling = useCallback(
    (id: string, ftype: GisFileType) => {
      if (pollRef.current) clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        try {
          const s = await gisGetDatasetStatus(id)
          setStatus(s.status)
          setParseError(s.parse_error ?? null)
          if (
            s.status === 'awaiting_mapping' &&
            ftype === 'vector_with_kato' &&
            !mappingDoneRef.current
          ) {
            if (pollRef.current) clearInterval(pollRef.current)
            // Карточка теперь содержит attribute_columns — подгружаем для таблицы маппинга.
            try {
              setDataset(await gisGetDataset(id))
            } catch (e) {
              setError(errMessage(e, 'Не удалось загрузить колонки датасета'))
            }
          } else if (s.status === 'ready') {
            if (pollRef.current) clearInterval(pollRef.current)
            if (ftype === 'raster') startIngest(id)
          } else if (s.status === 'failed') {
            if (pollRef.current) clearInterval(pollRef.current)
          }
        } catch (e) {
          if (pollRef.current) clearInterval(pollRef.current)
          setError(errMessage(e, 'Ошибка опроса статуса'))
        }
      }, 3000)
    },
    [startIngest],
  )

  const resetWizard = () => {
    if (pollRef.current) clearInterval(pollRef.current)
    if (ingestPollRef.current) clearInterval(ingestPollRef.current)
    setDataset(null)
    setMappingDone(false)
    mappingDoneRef.current = false
    setStatus(null)
    setParseError(null)
    setIngest(null)
    setUploadPct(null)
    setFile(null)
  }

  const handleCreate = async () => {
    if (!file || !categoryId || !name.trim()) return
    setCreating(true)
    setError(null)
    resetWizard()
    setUploadPct(0)
    const ftype = fileType
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('file_type', ftype)
      fd.append('category_id', categoryId)
      fd.append('name', name.trim())
      if (description.trim()) fd.append('description', description.trim())
      if (unit.trim()) fd.append('unit', unit.trim())
      // GUI-загрузка всегда не-автоматическая (флаг был нужен только для скриптовой заливки).
      fd.append('automated', 'false')

      const ds = await gisCreateDataset(fd, setUploadPct)
      setUploadPct(null)
      setDataset(ds)
      setStatus(ds.status)
      setParseError(ds.parse_error ?? null)

      if (ds.status === 'awaiting_mapping' && ftype === 'vector_with_kato') {
        // Колонки уже готовы — подгружаем полную карточку для таблицы маппинга.
        setDataset(await gisGetDataset(ds.id))
      } else if (ds.status === 'ready') {
        if (ftype === 'raster') startIngest(ds.id)
      } else if (ds.status !== 'failed') {
        // Обработка ещё идёт: ждём awaiting_mapping (KATO) или ready.
        startPolling(ds.id, ftype)
      }
    } catch (e) {
      setUploadPct(null)
      setError(errMessage(e, 'Не удалось создать датасет'))
    } finally {
      setCreating(false)
    }
  }

  const handleMappingSaved = () => {
    setMappingDone(true)
    mappingDoneRef.current = true
    if (dataset) startPolling(dataset.id, 'vector_with_kato')
  }

  if (!canUpload) {
    return (
      <div className="p-6">
        <h1 className="mb-2 text-2xl font-semibold">Загрузка геоданных</h1>
        <p className="text-sm text-gray-600">
          Доступ разрешён ролям admin / exp_ver / exp_dir / data_uploader.
        </p>
      </div>
    )
  }

  const needsMapping =
    dataset && fileType === 'vector_with_kato' && status === 'awaiting_mapping' && !mappingDone
  const isRaster = fileType === 'raster'

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Загрузка геоданных</h1>
          <p className="mt-1 text-sm text-gray-600">
            Импорт слоя в подсистему dssgis.dwh.kz: загрузка → (для KATO) маппинг → обработка.
          </p>
        </div>
        <Link
          href="/dashboard/data-intake/gis/catalog"
          className="rounded-lg border px-3 py-2 text-sm hover:bg-gray-50"
        >
          Каталог датасетов
        </Link>
      </div>

      {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      {/* Шаг 1: метаданные + файл */}
      <section className="space-y-4 rounded-lg border bg-white p-5">
        <h2 className="text-lg font-medium">1. Слой и метаданные</h2>
        {refError && (
          <div className="rounded-lg bg-yellow-50 p-3 text-sm text-yellow-800">
            {refError}{' '}
            <button onClick={loadCats} className="underline">
              повторить
            </button>
          </div>
        )}
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="mb-1 block text-sm font-medium">Категория (направление)</label>
            <select
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value)}
              className="w-full rounded-lg border px-3 py-2 text-sm"
            >
              <option value="">— выберите категорию —</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Тип данных</label>
            <select
              value={fileType}
              onChange={(e) => setFileType(e.target.value as GisFileType)}
              className="w-full rounded-lg border px-3 py-2 text-sm"
            >
              {(Object.keys(FILE_TYPE_LABEL) as GisFileType[]).map((t) => (
                <option key={t} value={t}>
                  {FILE_TYPE_LABEL[t]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Название</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Наименование показателя…"
              className="w-full rounded-lg border px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Единица измерения</label>
            <input
              type="text"
              value={unit}
              onChange={(e) => setUnit(e.target.value)}
              placeholder="га, чел., % …"
              className="w-full rounded-lg border px-3 py-2 text-sm"
            />
          </div>
          <div className="md:col-span-2">
            <label className="mb-1 block text-sm font-medium">Описание</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              className="w-full rounded-lg border px-3 py-2 text-sm"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <input
            type="file"
            accept={ACCEPT_BY_TYPE[fileType]}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm"
          />
          <button
            onClick={handleCreate}
            disabled={!file || !categoryId || !name.trim() || creating}
            className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white disabled:bg-gray-300"
          >
            {creating ? 'Загрузка…' : 'Загрузить'}
          </button>
        </div>

        {/* Прогресс байтового аплоада */}
        {uploadPct !== null && (
          <div>
            <div className="h-2 w-full overflow-hidden rounded bg-gray-200">
              <div
                className="h-full bg-primary-600 transition-all"
                style={{ width: `${uploadPct}%` }}
              />
            </div>
            <p className="mt-1 text-xs text-gray-500">Загрузка файла… {uploadPct}%</p>
          </div>
        )}

        <p className="text-xs text-gray-500">
          Форматы: вектор — Shapefile (.zip), GeoJSON, GeoPackage; растр — GeoTIFF (.tif/.tiff).
          Shapefile загружается единым ZIP-архивом (.shp, .dbf, .shx, .prj).
        </p>
      </section>

      {/* Шаг 2: KATO-маппинг */}
      {needsMapping && (
        <section className="space-y-4 rounded-lg border bg-white p-5">
          <h2 className="text-lg font-medium">2. KATO-маппинг</h2>
          <KatoMappingForm
            datasetId={dataset.id}
            attributeColumns={dataset.attribute_columns ?? []}
            onSaved={handleMappingSaved}
          />
        </section>
      )}

      {/* Шаг 3: обработка */}
      {dataset && !needsMapping && (
        <section className="space-y-3 rounded-lg border bg-white p-5">
          <h2 className="text-lg font-medium">3. Обработка</h2>
          <div className="flex flex-wrap items-center gap-4 text-sm">
            <span className="max-w-[320px] truncate">
              Датасет: <b>{dataset.name}</b>
            </span>
            <span>
              Статус: <StatusBadge status={status} />
            </span>
          </div>

          {status !== 'ready' && status !== 'failed' && (
            <p className="text-sm text-gray-500">
              {mappingDone ? 'Применяем маппинг' : 'Идёт обработка'}, статус обновляется
              автоматически…
            </p>
          )}
          {status === 'failed' && parseError && (
            <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{parseError}</div>
          )}

          {/* Инжест COG растра в тайл-сервис */}
          {status === 'ready' && isRaster && ingest && (
            <div className="space-y-1">
              {ingest.status === 'running' && (
                <>
                  <p className="text-sm text-gray-600">Загрузка растра в тайл-сервис…</p>
                  {ingest.total ? (
                    <div className="h-2 w-full overflow-hidden rounded bg-gray-200">
                      <div
                        className="h-full bg-primary-600 transition-all"
                        style={{
                          width: `${Math.round(((ingest.downloaded ?? 0) / ingest.total) * 100)}%`,
                        }}
                      />
                    </div>
                  ) : (
                    <div className="h-2 w-1/3 animate-pulse rounded bg-primary-300" />
                  )}
                </>
              )}
              {ingest.status === 'error' && (
                <div className="flex items-center gap-3">
                  <span className="text-sm text-red-700">
                    Не удалось загрузить растр в тайл-сервис: {ingest.error}
                  </span>
                  <button
                    onClick={() => startIngest(dataset.id)}
                    className="text-sm text-primary-600 hover:underline"
                  >
                    повторить
                  </button>
                </div>
              )}
            </div>
          )}

          {status === 'ready' && (!isRaster || ingest?.status === 'done') && (
            <div className="flex items-center gap-3">
              <span className="text-sm text-green-700">
                {isRaster ? 'Растр готов, тайлы доступны.' : 'Датасет готов.'}
              </span>
              <Link
                href="/dashboard/data-intake/gis/catalog"
                className="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white"
              >
                Открыть в каталоге
              </Link>
              <button onClick={resetWizard} className="text-sm text-primary-600 hover:underline">
                загрузить ещё
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
