'use client'

/**
 * Один показатель в дашборде направления — карта района + 2 баровые диаграммы.
 *
 * Раскладка соответствует спецификации из docs/dashboards/social_demog:
 *   карта (choropleth по районам, подпись данных)
 *   вертикальная гистограмма — динамика показателя для выбранного района (фильтр район)
 *   горизонтальная гистограмма — показатель по районам за выбранный год (фильтр год)
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import maplibregl, { type Map as MapType, type GeoJSONSource } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import {
  DATA_YEAR,
  fetchRegions,
  fetchApprovedValues,
  fetchIndicatorNorms,
  fetchDistrictBoundaries,
  fetchKatoDistricts,
  type Region,
  type IndicatorValuesListItem,
  type GeoJSONFeatureCollection,
} from '@/lib/api/backend'
import { isWebGLAvailable, toFriendlyMapError, WEBGL_ERROR_MESSAGE } from '@/lib/webgl'
import { sanitizeSvg } from '@/lib/sanitize'
import { formatValue, valueFormatter } from './chartKit'
import { inRegion } from '@/lib/regions/cases'
import {
  ComboBarLineChart,
  DynamicsLineChart,
  HorizontalBarChart,
  MultiLineChart,
  StackedHorizontalBarChart,
  StackedVerticalBarChart,
  VerticalBarChart,
} from './charts'
import { BASEMAP_STYLE, LABEL_FONT, removeBasemapBoundaries } from '@/lib/map/basemap'
import api from '@/lib/api'
import DistrictYearHeatmap from './DistrictYearHeatmap'

async function exportIndicatorXlsx(indicatorId: string, name: string): Promise<void> {
  const { data: blob } = await api.get<Blob>('/indicator-values/export', {
    params: { indicator_id: indicatorId, validation_status: 'approved' },
    responseType: 'blob',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${name.replace(/[\\/:*?"<>|]/g, ' ').slice(0, 80)}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}

const SKO_CENTER: [number, number] = [69.09, 54.87]
const SKO_ZOOM = 6.6
const MAP_HEIGHT = 420

interface IndicatorViewProps {
  indicatorId: string
  indicatorName: string
  unit?: string
  /** Базовый цвет для choropleth (hex). По умолчанию синий. */
  color?: string
  /**
   * Если задан — нижняя левая панель «Динамика по годам» рисуется не столбчатой
   * диаграммой по одному показателю, а линейным графиком с несколькими линиями
   * для выбранного района. Карта и рейтинг районов остаются по `indicatorId`.
   */
  lineSeries?: { id: string; name: string; color: string }[]
  /**
   * Если задан — обе диаграммы (динамика по годам и рейтинг районов) рисуются
   * стопками из этих серий (накопление). Карта остаётся по `indicatorId`.
   */
  stackedSeries?: { id: string; name: string; color: string }[]
  /**
   * Если задан — панель динамики рисуется комбинированной диаграммой: столбцы по
   * `indicatorId` (левая ось) + линия этого показателя на отдельной правой оси.
   */
  comboLine?: { id: string; name: string; color: string; unit?: string }
  /**
   * Если задан — под базовыми диаграммами добавляется горизонтальная гистограмма
   * «по секторам экономики» за выбранный год. Значение каждого сектора берётся за
   * выбранный год: сначала по выбранному району, иначе по областному (level=1)
   * показателю (секторные ЗП обычно отчитываются на уровне области).
   */
  sectorBars?: { id: string; name: string; fullName: string; color: string }[]
  /**
   * Если задан — поверх карты (хороплет остаётся по `indicatorId`) в центроиде
   * каждого района рисуется кольцевая диаграмма за выбранный год:
   *   - сегменты (в %) — доли `parts` (напр. занятость по группам ВЭД);
   *   - диаметр кольца пропорционален значению `size` (напр. занятое население).
   */
  donutOverlay?: {
    size: { id: string; name: string }
    parts: { id: string; name: string; color: string }[]
  }
  /**
   * Если задан — карта красится дискретно по бинам (тепловая карта/классы):
   * район получает цвет бина, в который попадает его значение за выбранный год
   * (min исключительно, max включительно; null = ±∞), вместо непрерывного
   * градиента. Под картой рисуется легенда. Используется климатическими
   * дашбордами (шкалы из lib/climate/colorScales).
   */
  colorBins?: { min: number | null; max: number | null; color: string; label: string }[]
  /**
   * Если задан — дополнительно загружается этот показатель и передаётся вторым
   * аргументом в valueTransform (для производных, напр. соотношения двух рядов).
   */
  secondaryIndicatorId?: string
  /**
   * Трансформация значений перед отображением (карта и обе диаграммы): из сырых
   * значений показателя (и опц. вторичного) формирует производные —
   * напр. отклонение от среднеобластного/нормы или соотношение периодов.
   * Должна вернуть массив того же формата (period_start/region_id/value).
   */
  valueTransform?: (
    primary: IndicatorValuesListItem[],
    secondary: IndicatorValuesListItem[],
  ) => IndicatorValuesListItem[]
  /**
   * Если true — на диаграмму «Динамика по годам» добавляется пунктирная линия
   * линейного тренда (МНК по отображаемым точкам). См. lib/climate/trend.
   */
  showTrend?: boolean
  /**
   * Если true — на «Динамику по годам» добавляется горизонтальная
   * среднемноголетняя линия (норма) для выбранного района из indicator-norms.
   * Имеет смысл только для «сырой» панели: при заданном valueTransform норма
   * (в исходных единицах) не соответствует преобразованным значениям — игнорируется.
   */
  showNorm?: boolean
  /**
   * Тип диаграммы «Динамика по годам»: 'bar' (по умолчанию) или 'line'.
   * Климатические дашборды используют 'line' (как в образцах заказчика).
   */
  dynamicsChart?: 'bar' | 'line'
  /**
   * Шаблоны заголовков диаграмм. Подстановки: {region} — «в Айыртауском районе»,
   * {regionName} — название района, {years} — «2003–2025 гг.», {year} — выбранный год.
   */
  titles?: { dynamics?: string; ranking?: string }
  /** Число знаков после запятой во всех значениях (без «тыс.»/«млн»). */
  decimals?: number
  /**
   * Базовый период нормы [с, по]: линия нормы на «Динамике» — среднее значений
   * выбранного района за период (вместо indicator_norms), подпись с периодом.
   */
  normPeriod?: [number, number]
  /** Панель отклонений: столбцы и ранжирование окрашиваются по знаку, нулевая линия. */
  diverging?: boolean
  /** Заголовок легенды шкалы на карте (с единицей измерения). */
  legendTitle?: string
  /** Заголовок карты над панелью; подстановки как в `titles` ({year} — выбранный год). */
  mapTitle?: string
}

export default function IndicatorView({
  indicatorId,
  indicatorName,
  unit,
  color = '#2563eb',
  lineSeries,
  stackedSeries,
  comboLine,
  sectorBars,
  donutOverlay,
  colorBins,
  secondaryIndicatorId,
  valueTransform,
  showTrend,
  showNorm,
  dynamicsChart = 'bar',
  titles,
  decimals,
  normPeriod,
  diverging,
  legendTitle,
  mapTitle,
}: IndicatorViewProps) {
  const fmt = useMemo(() => valueFormatter(decimals), [decimals])
  const [regions, setRegions] = useState<Region[]>([])
  const [rawValues, setRawValues] = useState<IndicatorValuesListItem[]>([])
  const [secondaryValues, setSecondaryValues] = useState<IndicatorValuesListItem[]>([])
  const [boundaries, setBoundaries] = useState<GeoJSONFeatureCollection | null>(null)
  // Соответствие КАТО-код района → region.id (БД). Источник — /kato?level=2.
  const [katoRegions, setKatoRegions] = useState<Map<string, string>>(new Map())
  const [seriesValues, setSeriesValues] = useState<Map<string, IndicatorValuesListItem[]>>(
    new Map(),
  )
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Среднемноголетние нормы по районам: region_id → norm_value (для линии нормы).
  const [normByRegion, setNormByRegion] = useState<Map<string, number>>(new Map())

  useEffect(() => {
    let cancel = false
    setLoading(true)
    setError(null)
    const loadAll = (id: string) => fetchApprovedValues({ indicator_id: id, period_type: 'year' })
    Promise.all([
      fetchRegions(),
      fetchDistrictBoundaries(),
      fetchKatoDistricts(),
      loadAll(indicatorId),
      secondaryIndicatorId ? loadAll(secondaryIndicatorId) : Promise.resolve([]),
    ])
      .then(([regs, bounds, katoMap, vs, sec]) => {
        if (cancel) return
        setRegions(regs)
        setBoundaries(bounds)
        setKatoRegions(katoMap)
        setRawValues(vs)
        setSecondaryValues(sec)
      })
      .catch((e: unknown) => {
        if (cancel) return
        setError((e as { message?: string })?.message ?? 'Ошибка загрузки')
      })
      .finally(() => {
        if (!cancel) setLoading(false)
      })
    return () => {
      cancel = true
    }
  }, [indicatorId, secondaryIndicatorId])

  // Производные значения для отображения (карта/диаграммы): применяем
  // valueTransform к сырым значениям (и вторичному показателю), иначе — как есть.
  const values = useMemo(
    () => (valueTransform ? valueTransform(rawValues, secondaryValues) : rawValues),
    [valueTransform, rawValues, secondaryValues],
  )

  // Дозагрузка значений для доп. серий: мульти-линия, стопки, combo-линия.
  const extraSeries = useMemo(
    () => [
      ...(lineSeries ?? []),
      ...(stackedSeries ?? []),
      ...(comboLine ? [comboLine] : []),
      ...(sectorBars ?? []),
      ...(donutOverlay ? [donutOverlay.size, ...donutOverlay.parts] : []),
    ],
    [lineSeries, stackedSeries, comboLine, sectorBars, donutOverlay],
  )
  const seriesIdsKey = extraSeries.map((s) => s.id).join(',')
  useEffect(() => {
    if (extraSeries.length === 0) {
      setSeriesValues(new Map())
      return
    }
    let cancel = false
    Promise.all(
      extraSeries.map(
        async (s) => [s.id, await fetchApprovedValues({ indicator_id: s.id, period_type: 'year' })] as const,
      ),
    )
      .then((entries) => {
        if (!cancel) setSeriesValues(new Map(entries))
      })
      .catch(() => {
        /* ошибку основного индикатора уже показываем; серии — best-effort */
      })
    return () => {
      cancel = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesIdsKey])

  // Загрузка среднемноголетних норм (только для «сырой» панели: при valueTransform
  // норма в исходных единицах не соответствует преобразованным значениям).
  useEffect(() => {
    if (!showNorm || valueTransform || normPeriod) {
      setNormByRegion(new Map())
      return
    }
    let cancel = false
    fetchIndicatorNorms({ indicator_id: indicatorId })
      .then((norms) => {
        if (cancel) return
        const m = new Map<string, number>()
        for (const n of norms) if (!m.has(n.region_id)) m.set(n.region_id, n.norm_value)
        setNormByRegion(m)
      })
      .catch(() => {
        /* норма — best-effort overlay */
      })
    return () => {
      cancel = true
    }
  }, [showNorm, valueTransform, indicatorId, normPeriod])

  const years = useMemo(
    () => Array.from(new Set(values.map((v) => v.period_start.slice(0, 4)))).sort(),
    [values],
  )
  const [selectedYear, setSelectedYear] = useState<string>('')
  // Фильтр карты по значению (ТЗ ФFE.1): районы вне диапазона приглушаются.
  const [valueMin, setValueMin] = useState('')
  const [valueMax, setValueMax] = useState('')
  useEffect(() => {
    if (years.length > 0 && !years.includes(selectedYear)) {
      setSelectedYear(years[years.length - 1])
    }
  }, [years, selectedYear])

  // Районы — только level>=2 (level=1 — область). Если данных по level>=2 нет,
  // показываем единственный уровень, который есть в values.
  const districts = useMemo(() => {
    const ids = new Set(values.map((v) => v.region_id))
    const inValues = regions.filter((r) => ids.has(r.id))
    const dists = inValues.filter((r) => r.level >= 2)
    const pool = dists.length > 0 ? dists : inValues
    return pool.sort((a, b) => a.name.localeCompare(b.name))
  }, [regions, values])

  const [selectedRegion, setSelectedRegion] = useState<string>('')
  useEffect(() => {
    if (districts.length > 0 && !districts.find((d) => d.id === selectedRegion)) {
      setSelectedRegion(districts[0].id)
    }
  }, [districts, selectedRegion])

  const valuesByRegionIdForYear = useMemo(() => {
    const m = new Map<string, number>()
    for (const v of values) {
      if (v.period_start.slice(0, 4) === selectedYear) m.set(v.region_id, v.value)
    }
    return m
  }, [values, selectedYear])

  // Джойн границ к данным — по КАТО-коду района (feature.properties.kato → region.id),
  // без хрупкого матчинга по названиям. katoRegions приходит из /kato?level=2.

  // kato → значение за выбранный год (для choropleth и подписей карты).
  const valuesByKato = useMemo(() => {
    const m = new Map<string, number>()
    if (!boundaries) return m
    for (const f of boundaries.features) {
      const kato = f.properties?.kato as string | undefined
      if (!kato) continue
      const rid = katoRegions.get(kato)
      if (!rid) continue
      const v = valuesByRegionIdForYear.get(rid)
      if (v !== undefined) m.set(kato, v)
    }
    return m
  }, [boundaries, katoRegions, valuesByRegionIdForYear])

  // kato → центроид полигона района (для размещения кольцевых диаграмм).
  const centroidByKato = useMemo(() => {
    const m = new Map<string, [number, number]>()
    if (!boundaries) return m
    for (const f of boundaries.features) {
      const kato = f.properties?.kato as string | undefined
      if (!kato) continue
      const c = featureCentroid(f.geometry)
      if (c) m.set(kato, c)
    }
    return m
  }, [boundaries])

  // kato → название района (name_ru) для подписей маркеров/попапов.
  const nameByKato = useMemo(() => {
    const m = new Map<string, string>()
    if (!boundaries) return m
    for (const f of boundaries.features) {
      const kato = f.properties?.kato as string | undefined
      const name = f.properties?.name_ru as string | undefined
      if (kato && name) m.set(kato, name)
    }
    return m
  }, [boundaries])

  // Границы всей области (bbox по всем районам) — чтобы при первой загрузке
  // карта показывала СКО целиком (fitBounds), независимо от ширины контейнера.
  const skoBounds = useMemo(() => boundsOfFeatureCollection(boundaries), [boundaries])

  // Кольцевые диаграммы по районам за выбранный год: для каждого района из
  // boundaries — значение size (диаметр) и доли parts (сегменты). Районы без size
  // или с нулевой суммой долей пропускаем.
  const donutByKato = useMemo(() => {
    const out = new Map<
      string,
      { regionId: string; size: number; parts: number[]; total: number; lngLat: [number, number] }
    >()
    if (!donutOverlay || !boundaries) return out
    const sizeFor = (rid: string) =>
      (seriesValues.get(donutOverlay.size.id) ?? []).find(
        (v) => v.region_id === rid && v.period_start.slice(0, 4) === selectedYear,
      )?.value
    const partFor = (pid: string, rid: string) =>
      (seriesValues.get(pid) ?? []).find(
        (v) => v.region_id === rid && v.period_start.slice(0, 4) === selectedYear,
      )?.value ?? 0
    for (const [kato, lngLat] of centroidByKato.entries()) {
      const rid = katoRegions.get(kato)
      if (!rid) continue
      const size = sizeFor(rid)
      if (size === undefined) continue
      const parts = donutOverlay.parts.map((p) => partFor(p.id, rid))
      const total = parts.reduce((a, b) => a + b, 0)
      if (total <= 0) continue
      out.set(kato, { regionId: rid, size, parts, total, lngLat })
    }
    return out
  }, [donutOverlay, boundaries, katoRegions, centroidByKato, seriesValues, selectedYear])

  const timeSeries = useMemo(() => {
    return values
      .filter((v) => v.region_id === selectedRegion)
      .sort((a, b) => a.period_start.localeCompare(b.period_start))
      .map((v) => ({ year: v.period_start.slice(0, 4), value: v.value }))
  }, [values, selectedRegion])

  // Данные мульти-линии для выбранного района: { x: год, y: значение, group: имя серии }.
  const multiLineData = useMemo(() => {
    if (!lineSeries || lineSeries.length === 0) return []
    const out: { x: string; y: number; group: string }[] = []
    for (const s of lineSeries) {
      const vs = seriesValues.get(s.id) ?? []
      for (const v of vs) {
        if (v.region_id !== selectedRegion) continue
        out.push({ x: v.period_start.slice(0, 4), y: v.value, group: s.name })
      }
    }
    return out.sort((a, b) => a.x.localeCompare(b.x))
  }, [lineSeries, seriesValues, selectedRegion])

  const districtRanking = useMemo(() => {
    return districts
      .map((d) => ({ id: d.id, name: d.name, value: valuesByRegionIdForYear.get(d.id) ?? null }))
      .filter((d): d is { id: string; name: string; value: number } => d.value !== null)
      .sort((a, b) => b.value - a.value)
  }, [districts, valuesByRegionIdForYear])

  // Стопки (накопление) — динамика выбранного района по годам.
  const stackedDynamics = useMemo(() => {
    if (!stackedSeries || stackedSeries.length === 0) return []
    const byYear = new Map<string, number[]>()
    stackedSeries.forEach((s, i) => {
      for (const v of seriesValues.get(s.id) ?? []) {
        if (v.region_id !== selectedRegion) continue
        const yr = v.period_start.slice(0, 4)
        const parts = byYear.get(yr) ?? new Array(stackedSeries.length).fill(0)
        parts[i] = v.value
        byYear.set(yr, parts)
      }
    })
    return Array.from(byYear.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([year, parts]) => ({ label: year, parts }))
  }, [stackedSeries, seriesValues, selectedRegion])

  // Стопки — рейтинг районов за выбранный год (сортировка по сумме).
  const stackedRanking = useMemo(() => {
    if (!stackedSeries || stackedSeries.length === 0) return []
    const byRegion = new Map<string, number[]>()
    stackedSeries.forEach((s, i) => {
      for (const v of seriesValues.get(s.id) ?? []) {
        if (v.period_start.slice(0, 4) !== selectedYear) continue
        const parts = byRegion.get(v.region_id) ?? new Array(stackedSeries.length).fill(0)
        parts[i] = v.value
        byRegion.set(v.region_id, parts)
      }
    })
    return districts
      .map((d) => ({ id: d.id, name: d.name, parts: byRegion.get(d.id) ?? null }))
      .filter((d): d is { id: string; name: string; parts: number[] } => d.parts !== null)
      .map((d) => ({ ...d, total: d.parts.reduce((a, b) => a + b, 0) }))
      .sort((a, b) => b.total - a.total)
  }, [stackedSeries, seriesValues, selectedYear, districts])

  // Combo — значения линии (2-я ось) для выбранного района по годам.
  const comboLineByYear = useMemo(() => {
    const m = new Map<string, number>()
    if (!comboLine) return m
    for (const v of seriesValues.get(comboLine.id) ?? []) {
      if (v.region_id === selectedRegion) m.set(v.period_start.slice(0, 4), v.value)
    }
    return m
  }, [comboLine, seriesValues, selectedRegion])

  // Данные для попапа «ЗП по секторам» конкретного района: значение каждого
  // сектора за выбранный год именно по этому району (без областного фолбэка —
  // попап показывает данные самого района). Отсортировано по убыванию, пустые
  // сектора отброшены.
  const sectorDataForRegion = useCallback(
    (regionId: string) => {
      if (!sectorBars || sectorBars.length === 0) return []
      return sectorBars
        .map((s) => {
          const v = (seriesValues.get(s.id) ?? []).find(
            (x) => x.region_id === regionId && x.period_start.slice(0, 4) === selectedYear,
          )
          return v
            ? { name: s.name, fullName: s.fullName, value: v.value, color: s.color }
            : null
        })
        .filter(
          (d): d is { name: string; fullName: string; value: number; color: string } =>
            d !== null,
        )
        .sort((a, b) => b.value - a.value)
    },
    [sectorBars, seriesValues, selectedYear],
  )

  // ── MapLibre ───────────────────────────────────────────────────────
  const mapContainer = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapType | null>(null)
  const popupRef = useRef<maplibregl.Popup | null>(null)
  const markersRef = useRef<import('maplibre-gl').Marker[]>([])
  const [mapReady, setMapReady] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)

  useEffect(() => {
    if (!mapContainer.current || mapRef.current) return
    if (!isWebGLAvailable()) {
      setMapError(WEBGL_ERROR_MESSAGE)
      return
    }
    let map: MapType | null = null
    try {
      map = new maplibregl.Map({
        container: mapContainer.current,
        style: BASEMAP_STYLE,
        center: SKO_CENTER,
        zoom: SKO_ZOOM,
        attributionControl: false,
      })
      // eslint-disable-next-line no-console
      console.info('[IndicatorView/Map] map created')
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[IndicatorView/Map] new Map() failed', e)
      setMapError(toFriendlyMapError((e as { message?: string })?.message))
      return
    }

    map.on('load', () => {
      // eslint-disable-next-line no-console
      console.info('[IndicatorView/Map] load event')
      mapRef.current = map!
      // Убрать гос-/админграницы подложки — конфликтуют с границей районов.
      removeBasemapBoundaries(map!)
      // Контейнер часто ещё имеет 0×0 в момент new Map(), без resize карта не видна.
      map!.resize()
      setMapReady(true)
    })
    map.on('error', (e) => {
      // eslint-disable-next-line no-console
      console.error('[IndicatorView/Map] error', e)
      const msg = (e as { error?: { message?: string } })?.error?.message
      if (msg) setMapError(toFriendlyMapError(msg))
    })

    const obs = new ResizeObserver(() => mapRef.current?.resize())
    if (mapContainer.current) obs.observe(mapContainer.current)
    return () => {
      obs.disconnect()
      map?.remove()
      mapRef.current = null
    }
  }, [])

  // Базовые слои районов + подписи (название + значение за год).
  useEffect(() => {
    if (!mapReady || !mapRef.current || !boundaries) return
    const map = mapRef.current
    if (map.getSource('districts-src')) return
    map.addSource('districts-src', {
      type: 'geojson',
      data: boundaries as Parameters<GeoJSONSource['setData']>[0],
    })
    map.addLayer({
      id: 'districts-fill',
      type: 'fill',
      source: 'districts-src',
      paint: { 'fill-color': '#94a3b8', 'fill-opacity': 0.15 },
    })
    map.addLayer({
      id: 'districts-outline',
      type: 'line',
      source: 'districts-src',
      paint: { 'line-color': '#475569', 'line-width': 1.6, 'line-opacity': 0.7 },
    })
    map.addLayer({
      id: 'districts-labels',
      type: 'symbol',
      source: 'districts-src',
      layout: {
        'text-field': [
          'concat',
          ['get', 'name_ru'],
          '\n',
          ['coalesce', ['get', '__val'], '—'],
        ],
        'text-font': LABEL_FONT,
        'text-size': 13,
        'text-anchor': 'center',
        'text-allow-overlap': false,
      },
      paint: {
        'text-color': '#0f172a',
        'text-halo-color': '#ffffff',
        'text-halo-width': 1.6,
      },
    })
    // Вписать всю область в кадр при первой отрисовке слоёв. resize() — на случай,
    // если контейнер (теперь ~половина ширины) ещё не получил финальный размер.
    if (skoBounds) {
      map.resize()
      map.fitBounds(skoBounds, { padding: 24, duration: 0 })
    }
  }, [mapReady, boundaries, skoBounds])

  // Choropleth + подписи значений за выбранный год.
  useEffect(() => {
    if (!mapReady || !mapRef.current || !boundaries) return
    const map = mapRef.current

    const features = boundaries.features.map((f) => {
      const kato = f.properties?.kato as string | undefined
      const val = kato !== undefined ? valuesByKato.get(kato) : undefined
      return {
        ...f,
        properties: {
          ...f.properties,
          __val: val !== undefined ? fmt(val) : null,
        },
      }
    })
    const src = map.getSource('districts-src') as GeoJSONSource | undefined
    if (src) {
      src.setData({
        type: 'FeatureCollection',
        features,
      } as Parameters<GeoJSONSource['setData']>[0])
    }

    const vals = Array.from(valuesByKato.values())
    if (!map.getLayer('districts-fill')) return
    if (vals.length === 0) {
      map.setPaintProperty('districts-fill', 'fill-color', '#94a3b8')
      map.setPaintProperty('districts-fill', 'fill-opacity', 0.15)
      return
    }
    const minVal = Math.min(...vals)
    const maxVal = Math.max(...vals)
    const matchExpr: unknown[] = ['match', ['get', 'kato']]
    const lo = valueMin.trim() === '' ? null : Number(valueMin.replace(',', '.'))
    const hi = valueMax.trim() === '' ? null : Number(valueMax.replace(',', '.'))
    for (const [kato, v] of valuesByKato.entries()) {
      let fill: string
      if ((lo !== null && !Number.isNaN(lo) && v < lo) || (hi !== null && !Number.isNaN(hi) && v > hi)) {
        fill = '#f1f5f9' // вне диапазона фильтра
      } else if (colorBins && colorBins.length > 0) {
        // Дискретная раскраска: цвет бина, в который попадает значение.
        const bin = colorBins.find(
          (b) => (b.min === null || v > b.min) && (b.max === null || v <= b.max),
        )
        fill = bin ? bin.color : '#e2e8f0'
      } else {
        const t = maxVal === minVal ? 0.5 : (v - minVal) / (maxVal - minVal)
        fill = blendColor(color, 0.2 + t * 0.7)
      }
      matchExpr.push(kato)
      matchExpr.push(fill)
    }
    matchExpr.push('#e2e8f0')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    map.setPaintProperty('districts-fill', 'fill-color', matchExpr as any)
    map.setPaintProperty('districts-fill', 'fill-opacity', colorBins ? 0.92 : 0.85)
  }, [mapReady, boundaries, valuesByKato, color, colorBins, fmt, valueMin, valueMax])

  // Кольцевые диаграммы районов как HTML-маркеры поверх карты. Диаметр кольца
  // линейно масштабируется по `size` (мин..макс среди районов), сегменты — доли
  // `parts`. Клик по кольцу выбирает район в фильтрах.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current
    markersRef.current.forEach((mk) => mk.remove())
    markersRef.current = []
    if (!donutOverlay || donutByKato.size === 0) return

    const sizes = Array.from(donutByKato.values()).map((d) => d.size)
    const sMin = Math.min(...sizes)
    const sMax = Math.max(...sizes)
    const D_MIN = 30
    const D_MAX = 64
    const colors = donutOverlay.parts.map((p) => p.color)

    for (const [kato, d] of donutByKato.entries()) {
      const t = sMax === sMin ? 0.5 : (d.size - sMin) / (sMax - sMin)
      const diameter = D_MIN + t * (D_MAX - D_MIN)
      const breakdown = donutOverlay.parts
        .map((p, i) => `${p.name}: ${((d.parts[i] / d.total) * 100).toFixed(0)}%`)
        .join(', ')
      const el = document.createElement('div')
      el.style.cursor = 'pointer'
      el.innerHTML = sanitizeSvg(donutSVG(diameter, d.parts, colors, d.total))
      el.title = `${nameByKato.get(kato) ?? ''}\n${donutOverlay.size.name}: ${formatValue(d.size)}\n${breakdown}`
      el.addEventListener('click', (ev) => {
        ev.stopPropagation()
        setSelectedRegion(d.regionId)
      })
      const mk = new maplibregl.Marker({ element: el }).setLngLat(d.lngLat).addTo(map)
      markersRef.current.push(mk)
    }
    return () => {
      markersRef.current.forEach((mk) => mk.remove())
      markersRef.current = []
    }
  }, [mapReady, donutOverlay, donutByKato, nameByKato])

  // Клик по району: выбрать его в фильтре (kato → region.id) и, если включены
  // секторы, показать на карте попап с горизонтальной гистограммой ЗП по секторам
  // именно этого района за выбранный год.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current
    function onClick(e: import('maplibre-gl').MapMouseEvent) {
      const f = map.queryRenderedFeatures(e.point, { layers: ['districts-fill'] })[0]
      if (!f) return
      const kato = f.properties?.kato as string | undefined
      const name = (f.properties?.name_ru as string) ?? '—'
      const rid = kato ? katoRegions.get(kato) : undefined
      if (rid) setSelectedRegion(rid)
      if (!sectorBars || sectorBars.length === 0 || !rid) return
      const data = sectorDataForRegion(rid)
      popupRef.current?.remove()
      popupRef.current = new maplibregl.Popup({
        maxWidth: '460px',
        closeButton: true,
        closeOnClick: false,
      })
        .setLngLat(e.lngLat)
        .setHTML(
          sectorPopupHTML(
            `${name} · ЗП по секторам${unit ? `, ${unit}` : ''}, ${selectedYear} год`,
            data,
            unit,
          ),
        )
        .addTo(map)
    }
    map.on('click', onClick)
    return () => {
      map.off('click', onClick)
    }
  }, [mapReady, katoRegions, sectorBars, sectorDataForRegion, selectedYear, unit])

  const selectedRegionName = districts.find((d) => d.id === selectedRegion)?.name ?? '—'
  // Диапазон лет ряда выбранного района — для заголовков графиков динамики.
  const dynamicsSpan = yearSpanLabel(timeSeries.map((d) => d.year))
  const multiSpan = yearSpanLabel(
    Array.from(new Set(multiLineData.map((d) => d.x))).sort(),
  )
  const fillTitle = (tpl: string) =>
    tpl
      .replace('{region}', inRegion(selectedRegionName))
      .replace('{regionName}', selectedRegionName)
      .replace('{years}', dynamicsSpan)
      .replace('{year}', selectedYear)
  // Заголовок динамики по умолчанию: «Динамика — {показатель}, {район}, {период}».
  const dynamicsTitle = titles?.dynamics
    ? fillTitle(titles.dynamics)
    : `Динамика — ${indicatorName}, ${selectedRegionName}${dynamicsSpan ? `, ${dynamicsSpan}` : ''}`
  const rankingTitle = titles?.ranking
    ? fillTitle(titles.ranking)
    : `Ранжирование районов по «${indicatorName}», ${selectedYear} год`
  // Норма за базовый период — из ряда выбранного района (только «сырые» значения).
  const periodNorm = useMemo(() => {
    if (!normPeriod || valueTransform) return undefined
    const vs = rawValues
      .filter((v) => v.region_id === selectedRegion)
      .filter((v) => {
        const y = Number(v.period_start.slice(0, 4))
        return y >= normPeriod[0] && y <= normPeriod[1]
      })
      .map((v) => v.value)
    return vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : undefined
  }, [normPeriod, valueTransform, rawValues, selectedRegion])
  const normValue = showNorm && !valueTransform
    ? (normPeriod ? periodNorm : normByRegion.get(selectedRegion))
    : undefined
  const normLabel = normPeriod
    ? `Среднемноголетнее значение (${normPeriod[0]}–${normPeriod[1]} гг.)`
    : undefined
  // Не дублируем единицу в хедере, если она уже входит в название показателя
  // (устраняет «°C (°C)»).
  const unitLc = (unit ?? '').trim().toLowerCase()
  const showUnitSuffix = !!unitLc && !indicatorName.trim().toLowerCase().endsWith(unitLc)
  const min = districtRanking.length ? districtRanking[districtRanking.length - 1] : null
  const max = districtRanking.length ? districtRanking[0] : null
  const noData = !loading && !error && values.length === 0

  // ── Правая панель: данные выбранного района (обновляется при клике по карте
  //    или выборе в фильтре «Район»). Всё из уже посчитанных наборов. ──────────
  const selValue = valuesByRegionIdForYear.get(selectedRegion)
  const rankIndex = districtRanking.findIndex((d) => d.id === selectedRegion)
  const selRank = rankIndex >= 0 ? rankIndex + 1 : null
  const rankTotal = districtRanking.length
  const oblastAvg = rankTotal
    ? districtRanking.reduce((a, d) => a + d.value, 0) / rankTotal
    : null
  const selDeviation =
    selValue !== undefined && oblastAvg !== null ? selValue - oblastAvg : null
  const selYoY =
    timeSeries.length > 1
      ? timeSeries[timeSeries.length - 1].value - timeSeries[timeSeries.length - 2].value
      : null
  const selSectorData =
    sectorBars && sectorBars.length > 0 ? sectorDataForRegion(selectedRegion) : []
  const signed = (v: number) => `${v >= 0 ? '+' : '−'}${fmt(Math.abs(v))}`
  const signClass = (v: number) => (v >= 0 ? 'text-emerald-600' : 'text-rose-600')

  return (
    <div className="space-y-4">
      {mapTitle && (
        <h3 className="text-base font-semibold text-gray-900">{fillTitle(mapTitle)}</h3>
      )}
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-base text-red-700">
          {error}
        </div>
      )}
      {noData && (
        <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-3 text-center text-base text-gray-500">
          Нет загруженных и одобренных значений для показателя «{indicatorName}».
        </div>
      )}
      <div className="flex flex-wrap items-center gap-4 rounded-xl bg-white shadow-sm ring-1 ring-gray-100 px-4 py-3">
        <label className="text-sm text-gray-600 flex items-center gap-2">
          Год:
          <select
            className="rounded border border-gray-300 bg-white px-2 py-1 text-base focus:border-primary-500 focus:outline-none"
            value={selectedYear}
            onChange={(e) => setSelectedYear(e.target.value)}
          >
            {years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm text-gray-600 flex items-center gap-2">
          Район:
          <select
            className="rounded border border-gray-300 bg-white px-2 py-1 text-base focus:border-primary-500 focus:outline-none"
            value={selectedRegion}
            onChange={(e) => setSelectedRegion(e.target.value)}
          >
            {districts.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm text-gray-600 flex items-center gap-1.5" title="Районы со значением вне диапазона на карте приглушаются">
          Значение от
          <input
            inputMode="decimal"
            value={valueMin}
            onChange={(e) => setValueMin(e.target.value)}
            className="w-20 rounded border border-gray-300 px-2 py-1 text-sm"
            placeholder="мин"
          />
          до
          <input
            inputMode="decimal"
            value={valueMax}
            onChange={(e) => setValueMax(e.target.value)}
            className="w-20 rounded border border-gray-300 px-2 py-1 text-sm"
            placeholder="макс"
          />
        </label>
        {years.length > 0 && Number(years[years.length - 1]) < DATA_YEAR && (
          <span className="rounded bg-amber-50 px-2 py-1 text-sm text-amber-700 ring-1 ring-amber-100">
            Последние данные — за {years[years.length - 1]} год
          </span>
        )}
        <div className="ml-auto text-sm text-gray-500">
          <span className="font-medium text-gray-700">{indicatorName}</span>
          {showUnitSuffix && <span className="ml-1 text-gray-400">({unit})</span>}
          {max && min && (
            <span className="ml-3 text-gray-400">
              максимальное значение:{' '}
              <span className="text-gray-700">{fmt(max.value)} ({max.name})</span>
              <span className="mx-1">·</span>
              минимальное значение:{' '}
              <span className="text-gray-700">{fmt(min.value)} ({min.name})</span>
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 overflow-hidden">
        <div
          style={{
            position: 'relative',
            width: '100%',
            height: MAP_HEIGHT,
          }}
        >
          <div
            ref={mapContainer}
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
          />
          {!mapReady && !mapError && (
            <div className="absolute inset-0 flex items-center justify-center bg-gray-50 text-base text-gray-400 pointer-events-none">
              {loading ? 'Загрузка данных…' : 'Загрузка карты…'}
            </div>
          )}
          {mapError && (
            <div className="absolute inset-0 flex items-center justify-center bg-red-50 px-6 text-center text-base text-red-700">
              <p className="max-w-md">{mapError}</p>
            </div>
          )}
          {donutOverlay && donutByKato.size > 0 && (
            <div className="absolute bottom-2 left-2 rounded-lg bg-white/90 px-3 py-2 text-sm text-gray-600 shadow ring-1 ring-gray-200">
              <div className="mb-1 font-medium text-gray-700">Занятость по группам ВЭД, %</div>
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {donutOverlay.parts.map((p) => (
                  <span key={p.id} className="inline-flex items-center gap-1.5">
                    <span
                      className="inline-block h-2.5 w-2.5 rounded-sm"
                      style={{ backgroundColor: p.color }}
                    />
                    {p.name}
                  </span>
                ))}
              </div>
              <div className="mt-1 text-gray-400">
                Диаметр кольца — «{donutOverlay.size.name}». Заливка района — «{indicatorName}».
              </div>
            </div>
          )}
          {donutOverlay && !loading && donutByKato.size === 0 && (
            <div className="absolute bottom-2 left-2 rounded-lg bg-white/90 px-3 py-2 text-sm text-gray-500 shadow ring-1 ring-gray-200">
              Нет данных по группам ВЭД за {selectedYear} — выберите более ранний год.
            </div>
          )}
          {colorBins && colorBins.length > 0 && (
            <div className="absolute bottom-2 right-2 max-w-[60%] rounded-lg bg-white/90 px-3 py-2 text-sm text-gray-600 shadow ring-1 ring-gray-200">
              <div className="mb-1 font-medium text-gray-700">
                {legendTitle ?? `Шкала: ${indicatorName}`}
              </div>
              <div className="flex flex-col gap-0.5">
                {colorBins.map((b, i) => (
                  <span key={i} className="inline-flex items-center gap-1.5">
                    <span
                      className="inline-block h-2.5 w-2.5 flex-shrink-0 rounded-sm ring-1 ring-black/10"
                      style={{ backgroundColor: b.color }}
                    />
                    {b.label}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="flex flex-col rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
        <div className="flex items-start justify-between gap-2">
          <div className="text-sm font-medium uppercase tracking-wide text-gray-400">
            Выбранный район
          </div>
          {/* Отчёт по показателю — все районы и годы в Excel (ТЗ Прил. 2, ч. 2) */}
          <button
            onClick={() => exportIndicatorXlsx(indicatorId, indicatorName)}
            className="text-xs text-primary-600 hover:underline"
            title="Все значения показателя по районам и годам"
          >
            Excel
          </button>
        </div>
        <h3 className="mt-0.5 text-xl font-semibold text-gray-800">{selectedRegionName}</h3>
        <div className="mt-1 text-sm text-gray-400">
          {indicatorName}
          {showUnitSuffix ? `, ${unit}` : ''} · {selectedYear}
        </div>

        <div className="mt-4 flex items-baseline gap-2">
          <span className="text-3xl font-semibold" style={{ color }}>
            {selValue !== undefined ? fmt(selValue) : '—'}
          </span>
          {selValue !== undefined && showUnitSuffix && (
            <span className="text-base text-gray-400">{unit}</span>
          )}
        </div>

        <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 text-base">
          <div>
            <dt className="text-sm text-gray-400">Ранг среди районов</dt>
            <dd className="font-medium text-gray-700">
              {selRank ? `${selRank} из ${rankTotal}` : '—'}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-gray-400">Среднее по области</dt>
            <dd className="font-medium text-gray-700">
              {oblastAvg !== null ? fmt(oblastAvg) : '—'}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-gray-400">Отклонение от среднего по области</dt>
            <dd className={`font-medium ${selDeviation !== null ? signClass(selDeviation) : 'text-gray-700'}`}>
              {selDeviation !== null ? signed(selDeviation) : '—'}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-gray-400">Изменение к предыдущему году</dt>
            <dd className={`font-medium ${selYoY !== null ? signClass(selYoY) : 'text-gray-700'}`}>
              {selYoY !== null ? signed(selYoY) : '—'}
            </dd>
          </div>
        </dl>

        {selSectorData.length > 0 && (
          <div className="mt-5">
            <div className="mb-1.5 text-sm font-medium text-gray-500">
              По секторам · {selectedYear}
            </div>
            <ul className="space-y-1">
              {selSectorData.slice(0, 6).map((s) => (
                <li key={s.name} className="flex items-center justify-between gap-2 text-sm">
                  <span className="inline-flex min-w-0 items-center gap-1.5">
                    <span
                      className="inline-block h-2.5 w-2.5 flex-shrink-0 rounded-sm"
                      style={{ backgroundColor: s.color }}
                    />
                    <span className="truncate text-gray-600" title={s.fullName}>
                      {s.name}
                    </span>
                  </span>
                  <span className="flex-shrink-0 font-medium text-gray-700">
                    {formatValue(s.value)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <p className="mt-auto pt-4 text-sm text-gray-400">
          Кликните район на карте или выберите его в фильтре «Район» — данные обновятся.
        </p>
      </section>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {lineSeries && lineSeries.length > 0 ? (
          <MultiLineChart
            title={`Динамика показателей — ${selectedRegionName}${multiSpan ? `, ${multiSpan}` : ''}`}
            data={multiLineData}
            series={lineSeries}
            unit={unit}
          />
        ) : stackedSeries && stackedSeries.length > 0 ? (
          <StackedVerticalBarChart
            title={dynamicsTitle}
            data={stackedDynamics}
            series={stackedSeries}
            unit={unit}
          />
        ) : comboLine ? (
          <ComboBarLineChart
            title={dynamicsTitle}
            bars={timeSeries}
            barColor={color}
            barLabel={indicatorName}
            barUnit={unit}
            lineByYear={comboLineByYear}
            lineColor={comboLine.color}
            lineLabel={comboLine.name}
            lineUnit={comboLine.unit}
          />
        ) : dynamicsChart === 'line' && !diverging ? (
          <DynamicsLineChart
            title={dynamicsTitle}
            data={timeSeries}
            color={color}
            unit={unit}
            showTrend={showTrend}
            norm={normValue}
            normLabel={normLabel}
            decimals={decimals}
          />
        ) : (
          <VerticalBarChart
            title={dynamicsTitle}
            data={timeSeries}
            color={color}
            unit={unit}
            showTrend={showTrend}
            norm={normValue}
            normLabel={normLabel}
            decimals={decimals}
            diverging={diverging}
          />
        )}
        {stackedSeries && stackedSeries.length > 0 ? (
          <StackedHorizontalBarChart
            title={rankingTitle}
            data={stackedRanking}
            series={stackedSeries}
            highlightId={selectedRegion}
            unit={unit}
          />
        ) : (
          <HorizontalBarChart
            title={rankingTitle}
            data={districtRanking}
            highlightId={selectedRegion}
            color={color}
            unit={unit}
            decimals={decimals}
            diverging={diverging}
          />
        )}
      </div>

      {districts.length > 1 && (
        <DistrictYearHeatmap
          title={`${indicatorName}: районы по годам`}
          districts={districts}
          values={values.filter((v) => districts.some((d) => d.id === v.region_id))}
          format={fmt}
          color={color}
          onSelect={(rid, y) => {
            setSelectedRegion(rid)
            setSelectedYear(y)
          }}
        />
      )}

      {sectorBars && sectorBars.length > 0 && (
        <p className="text-sm text-gray-400">
          Кликните по району на карте, чтобы увидеть ЗП по секторам экономики за {selectedYear}.
        </p>
      )}
    </div>
  )
}

// ── Helpers ────────────────────────────────────────────────────────────

/** «2005–2025 гг.» либо «2025 год» из отсортированного массива годов (строки). */
function yearSpanLabel(years: string[]): string {
  if (years.length === 0) return ''
  const a = years[0]
  const b = years[years.length - 1]
  return a === b ? `${a} год` : `${a}–${b} гг.`
}

function blendColor(hex: string, t: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  const mix = (c: number) => Math.round(255 - t * (255 - c))
  return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`
}

// Центроид GeoJSON-геометрии для размещения маркера. Для (Multi)Polygon берём
// площадно-взвешенный центроид наибольшего внешнего кольца; на вырожденных
// данных — среднее вершин.
/** BBox всей коллекции районов: [[west,south],[east,north]] — вход для map.fitBounds. */
function boundsOfFeatureCollection(
  fc: GeoJSONFeatureCollection | null | undefined,
): [[number, number], [number, number]] | null {
  if (!fc || !fc.features || fc.features.length === 0) return null
  let minLng = Infinity
  let minLat = Infinity
  let maxLng = -Infinity
  let maxLat = -Infinity
  const walk = (coords: unknown): void => {
    if (!Array.isArray(coords)) return
    if (typeof coords[0] === 'number' && typeof coords[1] === 'number') {
      const lng = coords[0] as number
      const lat = coords[1] as number
      if (lng < minLng) minLng = lng
      if (lat < minLat) minLat = lat
      if (lng > maxLng) maxLng = lng
      if (lat > maxLat) maxLat = lat
      return
    }
    for (const c of coords) walk(c)
  }
  for (const f of fc.features) {
    walk((f.geometry as { coordinates?: unknown } | undefined)?.coordinates)
  }
  if (!Number.isFinite(minLng) || !Number.isFinite(maxLng)) return null
  return [
    [minLng, minLat],
    [maxLng, maxLat],
  ]
}

function featureCentroid(
  geometry: { type?: string; coordinates?: unknown } | null | undefined,
): [number, number] | null {
  if (!geometry || !geometry.coordinates) return null
  let rings: number[][][] = []
  if (geometry.type === 'Polygon') {
    rings = geometry.coordinates as number[][][]
  } else if (geometry.type === 'MultiPolygon') {
    // Выбираем полигон с самым длинным внешним кольцом (≈ крупнейший по площади).
    const polys = geometry.coordinates as number[][][][]
    let best: number[][][] | null = null
    for (const p of polys) if (!best || p[0].length > best[0].length) best = p
    if (best) rings = best
  } else {
    return null
  }
  const ring = rings[0]
  if (!ring || ring.length === 0) return null
  let area = 0
  let cx = 0
  let cy = 0
  for (let i = 0, n = ring.length; i < n; i++) {
    const [x0, y0] = ring[i]
    const [x1, y1] = ring[(i + 1) % n]
    const cross = x0 * y1 - x1 * y0
    area += cross
    cx += (x0 + x1) * cross
    cy += (y0 + y1) * cross
  }
  if (Math.abs(area) < 1e-12) {
    // Вырожденный полигон — среднее вершин.
    const avg = ring.reduce((a, p) => [a[0] + p[0], a[1] + p[1]], [0, 0])
    return [avg[0] / ring.length, avg[1] / ring.length]
  }
  area *= 0.5
  return [cx / (6 * area), cy / (6 * area)]
}

// Кольцевая диаграмма (donut) как SVG-строка для HTML-маркера. Сегменты задаём
// через stroke-dasharray на окружности; диаметр D задаёт размер кольца.
function donutSVG(D: number, parts: number[], colors: string[], total: number): string {
  const stroke = Math.max(5, D * 0.26)
  const r = (D - stroke) / 2
  const C = 2 * Math.PI * r
  let acc = 0
  const segs = parts
    .map((v, i) => {
      const f = total > 0 ? v / total : 0
      const seg =
        `<circle cx="${D / 2}" cy="${D / 2}" r="${r}" fill="none" stroke="${colors[i] ?? '#94a3b8'}"` +
        ` stroke-width="${stroke}" stroke-dasharray="${(f * C).toFixed(2)} ${C.toFixed(2)}"` +
        ` stroke-dashoffset="${(-acc * C).toFixed(2)}" />`
      acc += f
      return seg
    })
    .join('')
  return (
    `<svg width="${D.toFixed(1)}" height="${D.toFixed(1)}" viewBox="0 0 ${D} ${D}"` +
    ` style="display:block;transform:rotate(-90deg);filter:drop-shadow(0 1px 1px rgba(0,0,0,.35))">` +
    `<circle cx="${D / 2}" cy="${D / 2}" r="${r}" fill="#ffffff" fill-opacity="0.9"/>${segs}</svg>`
  )
}

// ── Sector bars popup (ЗП по секторам экономики для района) ────────────
// Попап MapLibre принимает HTML-строку, поэтому гистограмму собираем как
// инлайн-SVG: строка = сектор, цвет из справочника, полное имя — в подсказке.

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function sectorPopupHTML(
  title: string,
  data: { name: string; fullName: string; value: number; color: string }[],
  unit?: string,
): string {
  const head = `<div style="font-weight:600;color:#0f172a;margin-bottom:6px">${escapeHtml(title)}</div>`
  if (data.length === 0) {
    return `<div style="font:14px system-ui,sans-serif">${head}<div style="color:#94a3b8">Нет данных по секторам за этот год</div></div>`
  }
  const rowH = 22
  const labelW = 150
  const valueW = 66
  const padL = labelW + 6
  const padR = valueW + 6
  const padT = 2
  const padB = 2
  const width = 420
  const height = padT + padB + data.length * rowH
  const innerW = width - padL - padR
  const vMax = Math.max(...data.map((d) => d.value), 1)
  const rows = data
    .map((d, i) => {
      const y = padT + i * rowH
      const w = ((d.value / vMax) * innerW).toFixed(1)
      const v = `${formatValue(d.value)}${unit ? ' ' + unit : ''}`
      return (
        `<text x="${labelW}" y="${y + rowH / 2 + 3}" text-anchor="end" font-size="12" fill="#374151">${escapeHtml(d.name)}</text>` +
        `<rect x="${padL}" y="${y + 3}" width="${w}" height="${rowH - 6}" rx="2" fill="${d.color}" opacity="0.85"><title>${escapeHtml(d.fullName)}: ${escapeHtml(v)}</title></rect>` +
        `<text x="${width - padR + 3}" y="${y + rowH / 2 + 3}" font-size="12" fill="#374151">${escapeHtml(formatValue(d.value))}</text>`
      )
    })
    .join('')
  return `<div style="font:14px system-ui,sans-serif">${head}<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}">${rows}</svg></div>`
}
