'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import dynamic from 'next/dynamic'
import Link from 'next/link'
import { useAuthStore } from '@/store/authStore'
import {
  gisListCategories,
  gisListAllDatasets,
  gisGetDataset,
  gisListObservations,
  gisGetDatasetGeojson,
  gisGetDatasetSld,
  gisListCategoryLinks,
  gisListDatasetLinks,
  gisUpsertCategoryLink,
  gisUpsertDatasetLink,
  gisDeleteDatasetLink,
  gisUpdateDataset,
  gisRasterStatistics,
  gisRasterIngest,
  gisRasterIngestStatus,
  fetchDirections,
  fetchIndicators,
  type GisCategory,
  type GisDataset,
  type GisDatasetDetail,
  type GisObservation,
  type GisCategoryLink,
  type GisDatasetLink,
  type GisFileType,
  type GisRasterIngestStatus,
  type GeoJsonFeatureCollection,
  type Indicator,
} from '@/lib/api/backend'
import type { Direction } from '@/types/direction'
import KatoMappingForm from '@/components/gis/KatoMappingForm'
import {
  buildChoropleth,
  datasetDates,
  hasChoroplethData,
  type ChoroplethLegend,
} from '@/lib/gis/choropleth'
import { colormapGradient, DEFAULT_RASTER_COLORMAP } from '@/lib/gis/rasterColormaps'
import { parseSld, type SldLegendItem, type SldStyle } from '@/lib/gis/sld'

const DatasetGeojsonMap = dynamic(() => import('@/components/gis/DatasetGeojsonMap'), {
  ssr: false,
  loading: () => <div className="h-full min-h-[320px] animate-pulse rounded-lg bg-gray-100" />,
})

const FILE_TYPE_LABEL: Record<GisFileType, string> = {
  vector: 'Вектор',
  vector_with_kato: 'Вектор + KATO',
  raster: 'Растр',
}

const FILE_TYPE_BADGE: Record<GisFileType, string> = {
  vector: 'bg-sky-100 text-sky-700',
  vector_with_kato: 'bg-violet-100 text-violet-700',
  raster: 'bg-amber-100 text-amber-700',
}

function errMessage(e: unknown, fallback: string): string {
  const err = e as { response?: { data?: { detail?: string } } }
  return err.response?.data?.detail ?? fallback
}

// Компактная подпись периода датасета для колонки «Дата» — по start_date/end_date
// из списка dssgis. Период в несколько лет → «2005–2023»; один год → «2023»;
// дат нет → «—».
function datasetYearsLabel(d: GisDataset): string {
  const from = d.start_date?.slice(0, 4)
  const to = d.end_date?.slice(0, 4)
  if (!from && !to) return '—'
  if (!from || !to || from === to) return from ?? to ?? '—'
  return `${from}–${to}`
}

function TypeBadge({ t }: { t: GisFileType }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs ${FILE_TYPE_BADGE[t] ?? 'bg-gray-100 text-gray-600'}`}>
      {FILE_TYPE_LABEL[t] ?? t}
    </span>
  )
}

export default function GisCatalogPage() {
  const { hasRole } = useAuthStore()
  const canView =
    hasRole('admin') || hasRole('exp_ver') || hasRole('exp_dir') || hasRole('data_uploader')
  const canEdit = canView

  // Справочники GIS + DSS
  const [categories, setCategories] = useState<GisCategory[]>([])
  const [datasets, setDatasets] = useState<GisDataset[]>([])
  const [categoryLinks, setCategoryLinks] = useState<GisCategoryLink[]>([])
  const [datasetLinks, setDatasetLinks] = useState<GisDatasetLink[]>([])
  const [directions, setDirections] = useState<Direction[]>([])

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Фильтры
  const [activeCategory, setActiveCategory] = useState<string | null>(null)
  const [fileType, setFileType] = useState<GisFileType | ''>('')
  const [search, setSearch] = useState('')

  // Детальная карточка
  const [selected, setSelected] = useState<GisDataset | null>(null)

  const loadAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [cats, allDatasets, catLinks, dsLinks, dirs] = await Promise.all([
        gisListCategories(),
        gisListAllDatasets(),
        gisListCategoryLinks(),
        gisListDatasetLinks(),
        fetchDirections(),
      ])
      setCategories(cats)
      setDatasets(allDatasets)
      setCategoryLinks(catLinks)
      setDatasetLinks(dsLinks)
      setDirections(dirs)
    } catch (e) {
      setError(errMessage(e, 'Не удалось загрузить каталог геоданных'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (canView) loadAll()
  }, [canView, loadAll])

  // Индексы связей для быстрого доступа
  const catLinkByGisId = useMemo(() => {
    const m = new Map<string, GisCategoryLink>()
    for (const l of categoryLinks) m.set(l.gis_category_id, l)
    return m
  }, [categoryLinks])

  const dsLinkByGisId = useMemo(() => {
    const m = new Map<string, GisDatasetLink>()
    for (const l of datasetLinks) m.set(l.gis_dataset_id, l)
    return m
  }, [datasetLinks])

  const countByCategory = useMemo(() => {
    const m = new Map<string, number>()
    for (const d of datasets) m.set(d.category_id, (m.get(d.category_id) ?? 0) + 1)
    return m
  }, [datasets])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return datasets.filter((d) => {
      if (activeCategory && d.category_id !== activeCategory) return false
      if (fileType && d.file_type !== fileType) return false
      if (q && !d.name.toLowerCase().includes(q)) return false
      return true
    })
  }, [datasets, activeCategory, fileType, search])

  const upsertCategoryLink = useCallback(
    async (cat: GisCategory, directionId: string) => {
      if (!directionId) return
      try {
        const link = await gisUpsertCategoryLink({
          gis_category_id: cat.id,
          direction_id: directionId,
          gis_category_code: cat.code ?? null,
          gis_category_name: cat.name,
        })
        setCategoryLinks((prev) => [
          ...prev.filter((l) => l.gis_category_id !== cat.id),
          link,
        ])
      } catch (e) {
        setError(errMessage(e, 'Не удалось сохранить связь категории'))
      }
    },
    [],
  )

  const onDatasetLinkChange = useCallback((link: GisDatasetLink | null, gisDatasetId: string) => {
    setDatasetLinks((prev) => {
      const rest = prev.filter((l) => l.gis_dataset_id !== gisDatasetId)
      return link ? [...rest, link] : rest
    })
  }, [])

  // Датасет отредактирован в dssgis (описание): обновляем список и открытую карточку.
  const onDatasetPatched = useCallback((updated: GisDataset) => {
    setDatasets((prev) => prev.map((d) => (d.id === updated.id ? { ...d, ...updated } : d)))
    setSelected((prev) => (prev && prev.id === updated.id ? { ...prev, ...updated } : prev))
  }, [])

  if (!canView) {
    return (
      <div className="p-6">
        <h1 className="mb-2 text-2xl font-semibold">Каталог геоданных</h1>
        <p className="text-sm text-gray-600">
          Доступ разрешён ролям admin / exp_ver / exp_dir / data_uploader.
        </p>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Каталог геоданных</h1>
          <p className="mt-1 text-sm text-gray-600">
            Датасеты подсистемы dssgis.dwh.kz. Категории = направления, датасеты = показатели.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/dashboard/data-intake/gis"
            className="rounded-lg border px-3 py-2 text-sm hover:bg-gray-50"
          >
            Загрузить слой
          </Link>
          <button onClick={loadAll} className="rounded-lg border px-3 py-2 text-sm hover:bg-gray-50">
            Обновить
          </button>
        </div>
      </div>

      {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        {/* Категории (= направления) */}
        <aside className="space-y-1 rounded-lg border bg-white p-3">
          <div className="flex items-center justify-between px-1 pb-2">
            <h2 className="text-sm font-medium">Категории</h2>
            <span className="text-xs text-gray-400">{datasets.length}</span>
          </div>
          <button
            onClick={() => setActiveCategory(null)}
            className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm ${
              activeCategory === null ? 'bg-primary-50 text-primary-700' : 'hover:bg-gray-50'
            }`}
          >
            <span>Все датасеты</span>
            <span className="text-xs text-gray-400">{datasets.length}</span>
          </button>
          {categories.map((c) => {
            const link = catLinkByGisId.get(c.id)
            return (
              <button
                key={c.id}
                onClick={() => setActiveCategory(c.id)}
                className={`w-full rounded-md px-2 py-1.5 text-left text-sm ${
                  activeCategory === c.id ? 'bg-primary-50 text-primary-700' : 'hover:bg-gray-50'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="truncate">{c.name}</span>
                  <span className="ml-2 shrink-0 text-xs text-gray-400">
                    {countByCategory.get(c.id) ?? 0}
                  </span>
                </div>
                <div className="mt-0.5 text-xs">
                  {link ? (
                    <span className="text-green-600">→ {link.direction_name}</span>
                  ) : (
                    <span className="text-gray-400">направление не задано</span>
                  )}
                </div>
              </button>
            )
          })}
        </aside>

        {/* Датасеты */}
        <section className="space-y-3">
          {/* Привязка выбранной категории к направлению */}
          {activeCategory && canEdit && (
            <CategoryMappingBar
              category={categories.find((c) => c.id === activeCategory) ?? null}
              link={catLinkByGisId.get(activeCategory) ?? null}
              directions={directions}
              onChange={upsertCategoryLink}
            />
          )}

          <div className="flex flex-wrap items-center gap-2">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск по названию…"
              className="min-w-[200px] flex-1 rounded-lg border px-3 py-2 text-sm"
            />
            <select
              value={fileType}
              onChange={(e) => setFileType(e.target.value as GisFileType | '')}
              className="rounded-lg border px-3 py-2 text-sm"
            >
              <option value="">Все типы</option>
              {(Object.keys(FILE_TYPE_LABEL) as GisFileType[]).map((t) => (
                <option key={t} value={t}>
                  {FILE_TYPE_LABEL[t]}
                </option>
              ))}
            </select>
            <span className="text-sm text-gray-500">{filtered.length}</span>
          </div>

          <div className="overflow-auto rounded-lg border bg-white">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-3 py-2 text-left">Датасет</th>
                  <th className="px-3 py-2 text-left">Тип</th>
                  <th className="px-3 py-2 text-left">Дата</th>
                  <th className="px-3 py-2 text-left">Ед.</th>
                  <th className="px-3 py-2 text-left">Статус</th>
                  <th className="px-3 py-2 text-left">Показатель DSS</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-gray-400">
                      Загрузка…
                    </td>
                  </tr>
                ) : filtered.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-gray-400">
                      Нет датасетов
                    </td>
                  </tr>
                ) : (
                  filtered.map((d) => {
                    const link = dsLinkByGisId.get(d.id)
                    return (
                      <tr
                        key={d.id}
                        onClick={() => setSelected(d)}
                        className="cursor-pointer border-t hover:bg-gray-50"
                      >
                        <td className="px-3 py-2">{d.name}</td>
                        <td className="px-3 py-2">
                          <TypeBadge t={d.file_type} />
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap text-gray-600">
                          {datasetYearsLabel(d)}
                        </td>
                        <td className="px-3 py-2 text-gray-600">{d.unit ?? '—'}</td>
                        <td className="px-3 py-2">
                          <span
                            className={`rounded-full px-2 py-0.5 text-xs ${
                              d.status === 'ready'
                                ? 'bg-green-100 text-green-700'
                                : 'bg-gray-100 text-gray-600'
                            }`}
                          >
                            {d.status}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          {link ? (
                            <span className="text-green-700">{link.indicator_name}</span>
                          ) : (
                            <span className="text-gray-400">не связан</span>
                          )}
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {selected && (
        <DatasetDrawer
          key={selected.id}
          dataset={selected}
          link={dsLinkByGisId.get(selected.id) ?? null}
          categoryLink={catLinkByGisId.get(selected.category_id) ?? null}
          directions={directions}
          canEdit={canEdit}
          onClose={() => setSelected(null)}
          onLinkChange={onDatasetLinkChange}
          onDatasetPatched={onDatasetPatched}
        />
      )}
    </div>
  )
}

// ── Привязка категории → направление ──────────────────────────

function CategoryMappingBar({
  category,
  link,
  directions,
  onChange,
}: {
  category: GisCategory | null
  link: GisCategoryLink | null
  directions: Direction[]
  onChange: (cat: GisCategory, directionId: string) => void
}) {
  if (!category) return null
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-white p-3 text-sm">
      <span className="font-medium">{category.name}</span>
      <span className="text-gray-400">→ направление DSS:</span>
      <select
        value={link?.direction_id ?? ''}
        onChange={(e) => onChange(category, e.target.value)}
        className="rounded-lg border px-3 py-1.5 text-sm"
      >
        <option value="">— выберите —</option>
        {directions.map((d) => (
          <option key={d.id} value={d.id}>
            {d.name}
          </option>
        ))}
      </select>
      {link && <span className="text-xs text-green-600">связано</span>}
    </div>
  )
}

// ── Легенда choropleth ────────────────────────────────────────

function Legend({ legend, unit }: { legend: ChoroplethLegend; unit: string | null }) {
  return (
    <div className="absolute bottom-2 right-2 max-w-[240px] rounded-lg bg-white/90 p-2 text-xs shadow ring-1 ring-black/5">
      {unit && <div className="mb-1 font-medium text-gray-700">Ед.: {unit}</div>}
      {legend.kind === 'continuous' ? (
        <>
          <div
            className="h-2.5 w-full rounded"
            style={{ background: `linear-gradient(to right, #ffffff, ${legend.color})` }}
          />
          <div className="mt-1 flex justify-between text-gray-600">
            <span>{legend.minLabel}</span>
            <span>{legend.maxLabel}</span>
          </div>
        </>
      ) : (
        <div className="space-y-0.5">
          {(legend.items ?? []).slice(0, 10).map((it) => (
            <div key={it.label} className="flex items-center gap-1.5">
              <span className="inline-block h-3 w-3 rounded-sm" style={{ backgroundColor: it.color }} />
              <span className="truncate text-gray-700">{it.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SldLegend({ items }: { items: SldLegendItem[] }) {
  return (
    <div className="absolute bottom-2 right-2 max-h-[60%] max-w-[260px] overflow-auto rounded-lg bg-white/90 p-2 text-xs shadow ring-1 ring-black/5">
      <div className="space-y-0.5">
        {items.map((it, i) => (
          <div key={`${it.label}:${i}`} className="flex items-start gap-1.5">
            <span
              className={`mt-0.5 inline-block h-3 w-3 shrink-0 ${it.kind === 'point' ? 'rounded-full' : 'rounded-sm'}`}
              style={{ backgroundColor: it.color, border: it.strokeColor ? `1px solid ${it.strokeColor}` : undefined }}
            />
            <span className="text-gray-700">{it.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Легенда растра (цветовая шкала titiler) ───────────────────

function RasterLegend({ min, max, unit }: { min: number; max: number; unit: string | null }) {
  const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })
  return (
    <div className="absolute bottom-2 right-2 max-w-[240px] rounded-lg bg-white/90 p-2 text-xs shadow ring-1 ring-black/5">
      {unit && <div className="mb-1 font-medium text-gray-700">Ед.: {unit}</div>}
      <div
        className="h-2.5 w-full rounded"
        style={{ background: colormapGradient(DEFAULT_RASTER_COLORMAP) }}
      />
      <div className="mt-1 flex justify-between text-gray-600">
        <span>{fmt.format(min)}</span>
        <span>{fmt.format(max)}</span>
      </div>
    </div>
  )
}

// ── Детальная карточка датасета ───────────────────────────────

type Tab = 'overview' | 'map' | 'mapping' | 'observations' | 'link'

function DatasetDrawer({
  dataset,
  link,
  categoryLink,
  directions,
  canEdit,
  onClose,
  onLinkChange,
  onDatasetPatched,
}: {
  dataset: GisDataset
  link: GisDatasetLink | null
  categoryLink: GisCategoryLink | null
  directions: Direction[]
  canEdit: boolean
  onClose: () => void
  onLinkChange: (link: GisDatasetLink | null, gisDatasetId: string) => void
  onDatasetPatched: (updated: GisDataset) => void
}) {
  const [tab, setTab] = useState<Tab>('overview')
  const [detail, setDetail] = useState<GisDatasetDetail | null>(null)
  const [geojson, setGeojson] = useState<GeoJsonFeatureCollection | null>(null)
  const [geojsonLoading, setGeojsonLoading] = useState(false)
  const [observations, setObservations] = useState<GisObservation[]>([])
  const [obsTotal, setObsTotal] = useState(0)
  const [obsPage, setObsPage] = useState(1)
  const [selectedDate, setSelectedDate] = useState<string>('')
  const [err, setErr] = useState<string | null>(null)

  const isKato = dataset.file_type === 'vector_with_kato'
  const isRaster = dataset.file_type === 'raster'

  // Choropleth: список дат из geojson и обогащение цветом по значению.
  const dates = useMemo(() => datasetDates(geojson), [geojson])
  const isChoro = useMemo(() => hasChoroplethData(geojson), [geojson])

  // По умолчанию — последняя доступная дата.
  useEffect(() => {
    if (dates.length && !dates.includes(selectedDate)) setSelectedDate(dates[dates.length - 1])
  }, [dates, selectedDate])

  // Стиль SLD из каталога — как на дашбордах. Строки атрибутов схлопываем по
  // пробелам так же, как бэкенд нормализует SLD, иначе категории с переносом
  // строки не совпадут с правилами.
  const [sldXml, setSldXml] = useState<string | null>(null)
  const styled = useMemo<{ fc: GeoJsonFeatureCollection; sld: SldStyle } | null>(() => {
    if (!geojson || !sldXml) return null
    let sld: SldStyle | null = null
    try {
      sld = parseSld(sldXml)
    } catch {
      return null
    }
    if (!sld) return null
    const fc: GeoJsonFeatureCollection = {
      ...geojson,
      features: geojson.features.map((f) => ({
        ...f,
        properties: Object.fromEntries(
          Object.entries(f.properties ?? {}).map(([k, v]) => [
            k,
            typeof v === 'string' ? v.split(/\s+/).filter(Boolean).join(' ') : v,
          ]),
        ),
      })),
    }
    // Стиль по отсутствующим атрибутам не применяем: объекты стали бы невидимыми.
    const props = new Set(Object.keys(fc.features[0]?.properties ?? {}))
    if (!sld.propertyNames.every((p) => props.has(p))) return null
    return { fc, sld }
  }, [geojson, sldXml])

  const choro = useMemo(() => {
    if (!geojson || !isChoro || !selectedDate) return null
    return buildChoropleth(geojson, selectedDate)
  }, [geojson, isChoro, selectedDate])

  // Растр: статистика (min/max) для легенды; null → COG не залит (показываем bbox).
  const [rasterStats, setRasterStats] = useState<{ min: number; max: number } | null>(null)
  const [rasterStatsLoaded, setRasterStatsLoaded] = useState(false)
  const [rasterReloadKey, setRasterReloadKey] = useState(0) // ремоунт карты после инжеста
  const [rasterIngest, setRasterIngest] = useState<GisRasterIngestStatus | null>(null)
  useEffect(() => {
    if (tab !== 'map' || !isRaster || rasterStatsLoaded) return
    gisRasterStatistics(dataset.id)
      .then(setRasterStats)
      .finally(() => setRasterStatsLoaded(true))
  }, [tab, isRaster, dataset.id, rasterStatsLoaded])

  useEffect(() => {
    setErr(null)
    gisGetDataset(dataset.id)
      .then(setDetail)
      .catch((e) => setErr(errMessage(e, 'Не удалось загрузить карточку датасета')))
  }, [dataset.id])

  // Пост-фактум KATO-маппинг: датасет ждёт привязки колонок (awaiting_mapping).
  const needsMapping = isKato && detail?.status === 'awaiting_mapping'
  const handleMappingSaved = useCallback(() => {
    gisGetDataset(dataset.id)
      .then((d) => {
        setDetail(d)
        setTab('overview')
      })
      .catch((e) => setErr(errMessage(e, 'Не удалось обновить карточку')))
  }, [dataset.id])

  // Забрать COG растра в тайл-сервис по требованию, затем перерисовать карту тайлами.
  const handleRasterIngest = useCallback(async () => {
    setRasterIngest({ status: 'running', downloaded: 0, total: null })
    try {
      let s = await gisRasterIngest(dataset.id)
      setRasterIngest(s)
      while (s.status === 'running') {
        await new Promise((r) => setTimeout(r, 2000))
        s = await gisRasterIngestStatus(dataset.id)
        setRasterIngest(s)
      }
      if (s.status === 'done') {
        // сброс статистики + ремоунт карты → drawRaster увидит тайлы вместо bbox.
        setRasterStats(null)
        setRasterStatsLoaded(false)
        setRasterReloadKey((k) => k + 1)
      }
    } catch (e) {
      setRasterIngest({ status: 'error', error: errMessage(e, 'Ошибка инжеста растра') })
    }
  }, [dataset.id])

  // Ленивая загрузка geojson при открытии вкладки «Карта» (не для raster).
  useEffect(() => {
    if (tab !== 'map' || isRaster || geojson || geojsonLoading) return
    setGeojsonLoading(true)
    Promise.all([gisGetDatasetGeojson(dataset.id), gisGetDatasetSld(dataset.id).catch(() => null)])
      .then(([fc, sldXml]) => {
        setGeojson(fc)
        setSldXml(sldXml)
      })
      .catch((e) => setErr(errMessage(e, 'Не удалось загрузить геометрию')))
      .finally(() => setGeojsonLoading(false))
  }, [tab, isRaster, dataset.id, geojson, geojsonLoading])

  // Наблюдения (только vector_with_kato).
  useEffect(() => {
    if (tab !== 'observations' || !isKato) return
    gisListObservations(dataset.id, { page: obsPage, page_size: 50 })
      .then((p) => {
        setObservations(p.data)
        setObsTotal(p.total)
      })
      .catch((e) => setErr(errMessage(e, 'Не удалось загрузить наблюдения')))
  }, [tab, isKato, dataset.id, obsPage])

  const tabs: { key: Tab; label: string; show: boolean }[] = [
    { key: 'overview', label: 'Обзор', show: true },
    { key: 'map', label: 'Карта', show: true },
    { key: 'mapping', label: 'КАТО-маппинг', show: !!needsMapping },
    { key: 'observations', label: 'Наблюдения', show: isKato },
    { key: 'link', label: 'Связь с DSS', show: true },
  ]

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-2xl flex-col bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b p-4">
          <div className="pr-4">
            <h2 className="text-lg font-semibold">{dataset.name}</h2>
            <div className="mt-1 flex items-center gap-2 text-sm">
              <TypeBadge t={dataset.file_type} />
              {dataset.unit && <span className="text-gray-500">{dataset.unit}</span>}
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label="Закрыть">
            ✕
          </button>
        </div>

        <div className="flex gap-1 border-b px-4">
          {tabs
            .filter((t) => t.show)
            .map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`px-3 py-2 text-sm ${
                  tab === t.key
                    ? 'border-b-2 border-primary-600 font-medium text-primary-700'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {t.label}
              </button>
            ))}
        </div>

        <div className="flex-1 overflow-auto p-4">
          {err && <div className="mb-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>}

          {tab === 'overview' && (
            <OverviewTab
              dataset={dataset}
              detail={detail}
              categoryLink={categoryLink}
              canEdit={canEdit}
              onDatasetPatched={onDatasetPatched}
            />
          )}

          {tab === 'map' && (
            <div className="space-y-2">
              {/* Выбор года для датасетов с несколькими датами */}
              {isChoro && dates.length > 1 && (
                <div className="flex items-center gap-2 text-sm">
                  <label className="text-gray-500">Год:</label>
                  <select
                    value={selectedDate}
                    onChange={(e) => setSelectedDate(e.target.value)}
                    className="rounded-lg border px-2 py-1 text-sm"
                  >
                    {dates.map((d) => (
                      <option key={d} value={d}>
                        {d.slice(0, 4)}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div className="relative h-[420px]">
                {isRaster ? (
                  <DatasetGeojsonMap
                    key={`${dataset.id}:${rasterReloadKey}`}
                    raster={{ id: dataset.id, bbox: detail?.bbox ?? null, unit: dataset.unit ?? null }}
                  />
                ) : geojsonLoading ? (
                  <div className="h-full animate-pulse rounded-lg bg-gray-100" />
                ) : styled ? (
                  <DatasetGeojsonMap key={`${dataset.id}:sld`} geojson={styled.fc} sld={styled.sld} />
                ) : isChoro && choro ? (
                  <DatasetGeojsonMap key={dataset.id} geojson={choro.fc} />
                ) : (
                  <DatasetGeojsonMap geojson={geojson} />
                )}

                {/* Легенда choropleth (вектор) */}
                {!isRaster && styled && styled.sld.legend.length > 0 && (
                  <SldLegend items={styled.sld.legend} />
                )}
                {!isRaster && !styled && choro?.legend && (
                  <Legend legend={choro.legend} unit={dataset.unit ?? null} />
                )}

                {/* Легенда растра (цветовая шкала titiler) */}
                {isRaster && rasterStats && (
                  <RasterLegend
                    min={rasterStats.min}
                    max={rasterStats.max}
                    unit={dataset.unit ?? null}
                  />
                )}
              </div>

              {isRaster && rasterStatsLoaded && !rasterStats && (
                <div className="space-y-2">
                  <p className="text-xs text-gray-500">
                    Растр ещё не загружен в тайл-сервис — показан контур (bbox). Пиксельные данные
                    появятся после загрузки COG.
                  </p>
                  {rasterIngest?.status === 'running' ? (
                    <div className="flex items-center gap-2 text-xs text-gray-600">
                      <span className="h-3 w-3 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
                      Загрузка растра в тайл-сервис…
                    </div>
                  ) : (
                    <button
                      onClick={handleRasterIngest}
                      disabled={!canEdit}
                      className="rounded-lg bg-primary-600 px-3 py-1.5 text-sm font-medium text-white disabled:bg-gray-300"
                    >
                      Загрузить в тайл-сервис
                    </button>
                  )}
                  {rasterIngest?.status === 'error' && (
                    <p className="text-xs text-red-600">
                      Не удалось загрузить растр: {rasterIngest.error}
                    </p>
                  )}
                </div>
              )}
              {isRaster && rasterStats && (
                <p className="text-xs text-gray-500">Клик по карте — значение в точке.</p>
              )}
            </div>
          )}

          {tab === 'mapping' && needsMapping && (
            <div className="space-y-3">
              <p className="text-sm text-gray-600">
                Датасет ожидает привязки KATO. Укажите колонку KATO и колонки-годы со значениями —
                после сохранения датасет будет обработан и появится на карте.
              </p>
              <KatoMappingForm
                datasetId={dataset.id}
                attributeColumns={detail?.attribute_columns ?? []}
                onSaved={handleMappingSaved}
              />
            </div>
          )}

          {tab === 'observations' && isKato && (
            <ObservationsTab
              observations={observations}
              total={obsTotal}
              page={obsPage}
              onPage={setObsPage}
            />
          )}

          {tab === 'link' && (
            <LinkTab
              dataset={dataset}
              link={link}
              categoryLink={categoryLink}
              directions={directions}
              canEdit={canEdit}
              onLinkChange={onLinkChange}
              onError={setErr}
            />
          )}
        </div>
      </div>
    </div>
  )
}

// Описание карты: редактируемое поле dataset.description прямо в dssgis (PATCH).
// Показ + правка под canEdit; сохраняется через /gis/datasets/{id}.
function MapDescription({
  dataset,
  canEdit,
  onDatasetPatched,
}: {
  dataset: GisDataset
  canEdit: boolean
  onDatasetPatched: (updated: GisDataset) => void
}) {
  const current = dataset.description ?? ''
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(current)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const startEdit = () => {
    setValue(dataset.description ?? '')
    setErr(null)
    setEditing(true)
  }

  const save = async () => {
    setSaving(true)
    setErr(null)
    try {
      const updated = await gisUpdateDataset(dataset.id, {
        description: value.trim() || null,
      })
      onDatasetPatched(updated)
      setEditing(false)
    } catch (e) {
      setErr(errMessage(e, 'Не удалось сохранить описание'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-lg border bg-gray-50 p-3">
      <div className="mb-1.5 flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-700">Описание карты</h3>
        {canEdit && !editing && (
          <button
            onClick={startEdit}
            className="text-xs font-medium text-primary-600 hover:text-primary-700"
          >
            {current ? 'Изменить' : 'Добавить'}
          </button>
        )}
      </div>

      {!editing ? (
        <p className="whitespace-pre-wrap break-words text-sm text-gray-800">
          {current || <span className="text-gray-400">— описание не задано</span>}
        </p>
      ) : (
        <div className="space-y-2">
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            rows={4}
            placeholder="Текстовое описание карты"
            className="w-full rounded-lg border px-3 py-2 text-sm focus:border-primary-500 focus:outline-none"
          />
          {err && <p className="text-xs text-red-600">{err}</p>}
          <div className="flex gap-2">
            <button
              onClick={save}
              disabled={saving}
              className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-40"
            >
              {saving ? 'Сохранение…' : 'Сохранить'}
            </button>
            <button
              onClick={() => { setEditing(false); setErr(null) }}
              disabled={saving}
              className="rounded-lg border px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-100 disabled:opacity-40"
            >
              Отмена
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function OverviewTab({
  dataset,
  detail,
  categoryLink,
  canEdit,
  onDatasetPatched,
}: {
  dataset: GisDataset
  detail: GisDatasetDetail | null
  categoryLink: GisCategoryLink | null
  canEdit: boolean
  onDatasetPatched: (updated: GisDataset) => void
}) {
  const rows: [string, string][] = [
    ['ID', dataset.id],
    ['Статус', dataset.status],
    ['Категория (направление DSS)', categoryLink?.direction_name ?? '— не задано'],
    ['Файл', detail?.filename ?? '—'],
    ['KATO-колонка', detail?.kato_column ?? '—'],
    ['Годы', (detail?.year_columns ?? []).map((y) => y.start_date?.slice(0, 4)).join(', ') || '—'],
  ]
  return (
    <div className="space-y-4">
      <MapDescription
        dataset={dataset}
        canEdit={canEdit}
        onDatasetPatched={onDatasetPatched}
      />

      <table className="w-full text-sm">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k} className="border-b last:border-0">
              <td className="py-1.5 pr-4 align-top text-gray-500">{k}</td>
              <td className="py-1.5 break-words font-medium">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {detail?.parse_error && (
        <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{detail.parse_error}</div>
      )}

      {detail?.attribute_columns && detail.attribute_columns.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-medium">Атрибуты</h3>
          <div className="overflow-auto rounded-lg border">
            <table className="min-w-full text-xs">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-2 py-1.5 text-left">Колонка</th>
                  <th className="px-2 py-1.5 text-left">Примеры</th>
                </tr>
              </thead>
              <tbody>
                {detail.attribute_columns.map((c) => (
                  <tr key={c.name} className="border-t">
                    <td className="px-2 py-1.5 font-medium">{c.name}</td>
                    <td className="px-2 py-1.5 text-gray-500">
                      {(c.samples ?? []).slice(0, 4).join(', ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function ObservationsTab({
  observations,
  total,
  page,
  onPage,
}: {
  observations: GisObservation[]
  total: number
  page: number
  onPage: (p: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / 50))
  return (
    <div className="space-y-3">
      <div className="overflow-auto rounded-lg border">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left">KATO</th>
              <th className="px-3 py-2 text-left">Дата</th>
              <th className="px-3 py-2 text-right">Значение</th>
            </tr>
          </thead>
          <tbody>
            {observations.length === 0 ? (
              <tr>
                <td colSpan={3} className="px-3 py-6 text-center text-gray-400">
                  Нет наблюдений
                </td>
              </tr>
            ) : (
              observations.map((o) => (
                <tr key={o.id} className="border-t">
                  <td className="px-3 py-1.5 font-mono text-xs">{o.kato_code}</td>
                  <td className="px-3 py-1.5">{o.end_date ? `${o.start_date} – ${o.end_date}` : o.start_date}</td>
                  <td className="px-3 py-1.5 text-right">{o.value_text ?? o.value ?? '—'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <button
            disabled={page <= 1}
            onClick={() => onPage(page - 1)}
            className="rounded border px-2 py-1 disabled:opacity-40"
          >
            ←
          </button>
          <span className="text-gray-500">
            {page} / {pages} · всего {total}
          </span>
          <button
            disabled={page >= pages}
            onClick={() => onPage(page + 1)}
            className="rounded border px-2 py-1 disabled:opacity-40"
          >
            →
          </button>
        </div>
      )}
    </div>
  )
}

function LinkTab({
  dataset,
  link,
  categoryLink,
  directions,
  canEdit,
  onLinkChange,
  onError,
}: {
  dataset: GisDataset
  link: GisDatasetLink | null
  categoryLink: GisCategoryLink | null
  directions: Direction[]
  canEdit: boolean
  onLinkChange: (link: GisDatasetLink | null, gisDatasetId: string) => void
  onError: (msg: string | null) => void
}) {
  // Направление по умолчанию — то, к которому привязана категория датасета.
  const [directionId, setDirectionId] = useState<string>(
    link?.direction_id ?? categoryLink?.direction_id ?? '',
  )
  const [indicators, setIndicators] = useState<Indicator[]>([])
  const [indicatorId, setIndicatorId] = useState<string>(link?.indicator_id ?? '')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!directionId) {
      setIndicators([])
      return
    }
    fetchIndicators({ direction_id: directionId, is_active: true })
      .then(setIndicators)
      .catch(() => setIndicators([]))
  }, [directionId])

  const save = async () => {
    if (!indicatorId) return
    setSaving(true)
    onError(null)
    try {
      const newLink = await gisUpsertDatasetLink({
        gis_dataset_id: dataset.id,
        indicator_id: indicatorId,
        gis_dataset_name: dataset.name,
        gis_category_id: dataset.category_id,
      })
      onLinkChange(newLink, dataset.id)
    } catch (e) {
      onError(errMessage(e, 'Не удалось сохранить связь'))
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    setSaving(true)
    onError(null)
    try {
      await gisDeleteDatasetLink(dataset.id)
      onLinkChange(null, dataset.id)
      setIndicatorId('')
    } catch (e) {
      onError(errMessage(e, 'Не удалось удалить связь'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-600">
        Свяжите датасет GIS с показателем DSS. Датасет = показатель; несколько датасетов (например
        по годам) могут ссылаться на один показатель.
      </p>

      {link && (
        <div className="rounded-lg bg-green-50 p-3 text-sm text-green-800">
          Текущая связь: <b>{link.indicator_name}</b>
        </div>
      )}

      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-sm font-medium">Направление</label>
          <select
            value={directionId}
            onChange={(e) => {
              setDirectionId(e.target.value)
              setIndicatorId('')
            }}
            disabled={!canEdit}
            className="w-full rounded-lg border px-3 py-2 text-sm"
          >
            <option value="">— выберите направление —</option>
            {directions.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">Показатель</label>
          <select
            value={indicatorId}
            onChange={(e) => setIndicatorId(e.target.value)}
            disabled={!canEdit || !directionId}
            className="w-full rounded-lg border px-3 py-2 text-sm"
          >
            <option value="">— выберите показатель —</option>
            {indicators.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name}
              </option>
            ))}
          </select>
        </div>

        {canEdit && (
          <div className="flex items-center gap-2">
            <button
              onClick={save}
              disabled={!indicatorId || saving}
              className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white disabled:bg-gray-300"
            >
              {saving ? 'Сохранение…' : 'Сохранить связь'}
            </button>
            {link && (
              <button
                onClick={remove}
                disabled={saving}
                className="rounded-lg border px-4 py-2 text-sm text-red-600 hover:bg-red-50"
              >
                Удалить связь
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
