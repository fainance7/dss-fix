'use client'

import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import {
  fetchDirections,
  fetchDistrictBoundaries,
  fetchIndicators,
  fetchKatoDistricts,
  fetchRegions,
  type Indicator,
  type IndicatorValuesListItem,
} from '@/lib/api/backend'
import { fetchAllIndicatorValues, formatNumber, normalizePopulation } from '@/lib/indicators/overview'
import type { Direction } from '@/types/direction'
import { isWebGLAvailable, WEBGL_ERROR_MESSAGE } from '@/lib/webgl'
import { sanitizeSvg } from '@/lib/sanitize'
import { BASEMAP_STYLE, LABEL_FONT, removeBasemapBoundaries } from '@/lib/map/basemap'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Map as MapType, GeoJSONSource, LngLatLike } from 'maplibre-gl'

// ─── Constants ───────────────────────────────────────────────────────
const SKO_CENTER: [number, number] = [69.09, 54.87]
const SKO_ZOOM = 7.5

type VizType = 'choropleth' | 'vbar' | 'hbar' | 'pie' | 'prop-pie'

const VIZ_OPTIONS: { value: VizType; label: string }[] = [
  { value: 'choropleth', label: 'Картограмма (градиент)' },
  { value: 'vbar', label: 'Верт. гистограмма (2-3 показателя)' },
  { value: 'hbar', label: 'Гориз. гистограмма (3-4 показателя)' },
  { value: 'pie', label: 'Круговая диаграмма (2-7 долей)' },
  { value: 'prop-pie', label: 'Пропорциональная диаграмма' },
]

// Centroids computed at runtime from district boundary GeoJSON
// (populated after fetchDistrictBoundaries resolves)

/** Compute centroid of a GeoJSON polygon/multipolygon ring set */
function computeCentroid(geometry: { type: string; coordinates: number[][][] | number[][][][] }): [number, number] {
  // Collect all coordinate rings
  let rings: number[][][]
  if (geometry.type === 'MultiPolygon') {
    rings = (geometry.coordinates as number[][][][]).map((poly) => poly[0])
  } else {
    rings = [(geometry.coordinates as number[][][])[0]]
  }

  // Pick the largest ring by area (for multipolygons with islands)
  let bestRing = rings[0]
  let bestArea = 0
  for (const ring of rings) {
    let a = 0
    for (let i = 0; i < ring.length - 1; i++) {
      a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    }
    if (Math.abs(a) > bestArea) {
      bestArea = Math.abs(a)
      bestRing = ring
    }
  }

  // Compute centroid of the largest ring
  let cx = 0, cy = 0, area = 0
  for (let i = 0; i < bestRing.length - 1; i++) {
    const x0 = bestRing[i][0], y0 = bestRing[i][1]
    const x1 = bestRing[i + 1][0], y1 = bestRing[i + 1][1]
    const cross = x0 * y1 - x1 * y0
    area += cross
    cx += (x0 + x1) * cross
    cy += (y0 + y1) * cross
  }
  area /= 2
  if (Math.abs(area) < 1e-10) {
    // Fallback: simple average
    const avgX = bestRing.reduce((s, p) => s + p[0], 0) / bestRing.length
    const avgY = bestRing.reduce((s, p) => s + p[1], 0) / bestRing.length
    return [avgX, avgY]
  }
  cx /= (6 * area)
  cy /= (6 * area)
  return [cx, cy]
}

// Manual offsets [lng, lat] for districts whose computed centroid overlaps neighbors
const CENTROID_OFFSETS: Record<string, [number, number]> = {
  '595000000': [0.25, 0.15], // Кызылжарский: сместить СВ, чтобы не наложился на Петропавловск
}

/** Центроиды районов из GeoJSON границ, ключ — код КАТО (джойн с данными только по КАТО). */
function extractCentroids(geojson: { features: Array<{ properties: Record<string, unknown>; geometry: { type: string; coordinates: unknown } }> }): Record<string, [number, number]> {
  const result: Record<string, [number, number]> = {}
  for (const feature of geojson.features) {
    const kato = String(feature.properties?.kato ?? '')
    if (!kato) continue
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const [cx, cy] = computeCentroid(feature.geometry as any)
    const offset = CENTROID_OFFSETS[kato]
    result[kato] = offset ? [cx + offset[0], cy + offset[1]] : [cx, cy]
  }
  return result
}

// ─── Реальные данные ─────────────────────────────────────────────────

interface DistrictData {
  kato: string
  district: string // название района
  mainValue: number // для картограммы и размера пропорциональной диаграммы
  values: number[]
  labels: string[]
  colors: string[]
}

const SERIES_COLORS = ['#3b82f6', '#ef4444', '#f59e0b', '#22c55e', '#8b5cf6', '#ec4899', '#06b6d4']
const BASE_COLORS: Record<string, string> = {
  labor: '#3b82f6', social_demog: '#8b5cf6', water: '#06b6d4', economy: '#22c55e', ecology: '#10b981',
  tourism: '#f59e0b', transport: '#6366f1', climate: '#0ea5e9', agriculture: '#65a30d', land: '#a16207',
}

// Наборы для диаграмм: показатели ОДНОЙ единицы с данными по районам (сверено с
// данными прода). Для направления без набора диаграммы недоступны — только картограмма.
const MULTI_PRESETS: Record<string, Partial<Record<Exclude<VizType, 'choropleth'>, string[]>>> = {
  labor: {
    vbar: ['Численность занятого населения', 'Численность безработного населения', 'Численность самозанятых'],
    hbar: ['Численность наёмных работников', 'Численность самозанятых', 'Численность безработного населения', 'Лица не в составе рабочей силы'],
    pie: ['Численность наёмных работников', 'Численность самозанятых', 'Численность безработного населения'],
    'prop-pie': ['Численность наёмных работников', 'Численность самозанятых', 'Численность безработного населения'],
  },
  economy: {
    vbar: ['Валовая продукция промышленности', 'Валовая продукция сельского хозяйства', 'Объем инвестиций в основной капитал'],
    hbar: ['Валовая продукция промышленности', 'Валовая продукция сельского хозяйства', 'Объем инвестиций в основной капитал', 'Объем розничной торговли'],
    pie: ['Валовая продукция промышленности', 'Валовая продукция сельского хозяйства', 'Объем розничной торговли'],
    'prop-pie': ['Валовая продукция промышленности', 'Валовая продукция сельского хозяйства', 'Объем розничной торговли'],
  },
  social_demog: {
    vbar: ['Число родившихся (все население)', 'Число умерших (все население)'],
    hbar: ['Число школ', 'Число дошкольных организаций', 'Число больничных организаций'],
    pie: ['Численность казахов', 'Численность русских', 'Численность украинцев', 'Численность немцев', 'Численность татар', 'Численность поляков', 'Численность белорусов'],
    'prop-pie': ['Численность казахов', 'Численность русских', 'Численность украинцев', 'Численность немцев', 'Численность татар', 'Численность поляков', 'Численность белорусов'],
  },
  agriculture: {
    vbar: ['Посевная площадь зерновых культур', 'Посевная площадь масличных культур', 'Посевная площадь картофеля'],
    hbar: ['Посевная площадь зерновых культур', 'Посевная площадь масличных культур', 'Посевная площадь картофеля', 'Посевная площадь овощных культур'],
    pie: ['Посевная площадь зерновых культур', 'Посевная площадь масличных культур', 'Посевная площадь картофеля', 'Посевная площадь овощных культур'],
    'prop-pie': ['Посевная площадь зерновых культур', 'Посевная площадь масличных культур', 'Посевная площадь картофеля', 'Посевная площадь овощных культур'],
  },
  land: {
    vbar: ['Площадь пашни', 'Площадь пастбищ', 'Сенокосы'],
    hbar: ['Площадь пашни', 'Площадь пастбищ', 'Сенокосы', 'Залежь'],
    pie: ['Площадь пашни', 'Площадь пастбищ', 'Сенокосы', 'Залежь', 'Многолетние насаждения'],
    'prop-pie': ['Площадь пашни', 'Площадь пастбищ', 'Сенокосы', 'Залежь', 'Многолетние насаждения'],
  },
  climate: {
    vbar: ['Сумма осадков за вегетационный период', 'Сумма осадков за холодный период'],
    hbar: ['Сумма осадков за вегетационный период', 'Сумма осадков за холодный период'],
    pie: ['Сумма осадков за вегетационный период', 'Сумма осадков за холодный период'],
    'prop-pie': ['Сумма осадков за вегетационный период', 'Сумма осадков за холодный период'],
  },
  water: {
    vbar: ['Озёр площадью более 100 га', 'Озёр площадью до 100 га', 'Других водоёмов'],
    hbar: ['Озёр площадью более 100 га', 'Озёр площадью до 100 га', 'Других водоёмов'],
    pie: ['Озёр площадью более 100 га', 'Озёр площадью до 100 га', 'Других водоёмов'],
    'prop-pie': ['Озёр площадью более 100 га', 'Озёр площадью до 100 га', 'Других водоёмов'],
  },
  transport: {
    vbar: [
      'Расстояние до международного аэропорта Кызыл-Жар (г.Петропавловск)',
      'Расстояние до аэропорта Кокшетау',
      'Расстояние до международного аэропорта Нурсултана Назарбаева (г.Астана)',
    ],
    hbar: [
      'Расстояние до международного аэропорта Кызыл-Жар (г.Петропавловск)',
      'Расстояние до аэропорта Кокшетау',
      'Расстояние до международного аэропорта Нурсултана Назарбаева (г.Астана)',
      'Расстояние до международного аэропорта Костанай имени Ахмета Байтурсынова',
    ],
  },
}

// Показатель картограммы по умолчанию (иначе — первый в списке направления).
const CHOROPLETH_DEFAULT: Record<string, string> = {
  labor: 'Уровень безработицы',
  economy: 'Объем инвестиций в основной капитал',
  social_demog: 'Численность населения (всего)',
  agriculture: 'Урожайность зерновых культур',
  land: 'Площадь пашни',
  climate: 'Годовая сумма осадков',
  water: 'Озёр площадью более 100 га',
  ecology: 'Экологический след',
  transport: 'Оценка доступности аэропорта (шкала 1-5)',
}

// Короткая подпись серии в легенде: без общего префикса вроде «Численность ».
const ETHNIC_LABELS: Record<string, string> = {
  'Численность казахов': 'Казахи', 'Численность русских': 'Русские', 'Численность украинцев': 'Украинцы',
  'Численность немцев': 'Немцы', 'Численность татар': 'Татары', 'Численность поляков': 'Поляки',
  'Численность белорусов': 'Белорусы',
}

function shortLabel(name: string): string {
  if (ETHNIC_LABELS[name]) return ETHNIC_LABELS[name]
  const short = name
    .replace(/^Численность /, '')
    .replace(/^Посевная площадь /, '')
    .replace(/^Расстояние до (международного )?аэропорта /, '')
    .replace(/^Валовая продукция /, '')
  return short.charAt(0).toUpperCase() + short.slice(1)
}

// Часть рядов численности населения загружена в тысячах, часть — в людях.
function valueNormalizer(name: string): (v: number) => number {
  return name.startsWith('Численность населения') ? normalizePopulation : (v) => v
}

// ─── SVG Chart renderers ─────────────────────────────────────────────

function renderVerticalBar(values: number[], colors: string[], size: number): string {
  const barW = size / (values.length * 1.5 + 0.5)
  const gap = barW * 0.5
  const maxVal = Math.max(...values, 1)
  const maxH = size * 0.9
  let bars = ''
  values.forEach((v, i) => {
    const h = (v / maxVal) * maxH
    const x = gap / 2 + i * (barW + gap)
    const y = size - h
    bars += `<rect x="${x}" y="${y}" width="${barW}" height="${h}" fill="${colors[i]}" rx="2"/>`
  })
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${bars}</svg>`
}

function renderHorizontalBar(values: number[], colors: string[], size: number): string {
  const barH = size / (values.length * 1.5 + 0.5)
  const gap = barH * 0.5
  const maxVal = Math.max(...values, 1)
  const maxW = size * 0.9
  let bars = ''
  values.forEach((v, i) => {
    const w = (v / maxVal) * maxW
    const y = gap / 2 + i * (barH + gap)
    bars += `<rect x="0" y="${y}" width="${w}" height="${barH}" fill="${colors[i]}" rx="2"/>`
  })
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${bars}</svg>`
}

function renderPie(values: number[], colors: string[], size: number): string {
  const total = values.reduce((a, b) => a + b, 0)
  if (total === 0) return ''
  const r = size / 2 - 2
  const cx = size / 2
  const cy = size / 2
  let startAngle = -Math.PI / 2
  let paths = ''

  values.forEach((v, i) => {
    const slice = (v / total) * 2 * Math.PI
    const endAngle = startAngle + slice
    const x1 = cx + r * Math.cos(startAngle)
    const y1 = cy + r * Math.sin(startAngle)
    const x2 = cx + r * Math.cos(endAngle)
    const y2 = cy + r * Math.sin(endAngle)
    const large = slice > Math.PI ? 1 : 0
    paths += `<path d="M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large} 1 ${x2},${y2} Z" fill="${colors[i]}"/>`
    startAngle = endAngle
  })

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${paths}</svg>`
}

// ─── Component ───────────────────────────────────────────────────────

export default function DirectionMapPage() {
  const mapContainer = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapType | null>(null)
  const markersRef = useRef<import('maplibre-gl').Marker[]>([])

  const [mapReady, setMapReady] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)
  const [directions, setDirections] = useState<Direction[]>([])
  const [selectedDir, setSelectedDir] = useState<string>('')
  const [vizType, setVizType] = useState<VizType>('choropleth')
  const [tooltip, setTooltip] = useState<{ x: number; y: number; district: string; values: number[]; labels: string[]; unit: string } | null>(null)
  const [centroids, setCentroids] = useState<Record<string, [number, number]>>({})
  const [katoRegion, setKatoRegion] = useState<Map<string, string>>(new Map())
  const [regionNames, setRegionNames] = useState<Map<string, string>>(new Map())
  const [dirIndicators, setDirIndicators] = useState<Indicator[]>([])
  const [chIndicatorId, setChIndicatorId] = useState<string>('')
  const [valuesCache, setValuesCache] = useState<Record<string, IndicatorValuesListItem[]>>({})
  const [loadingValues, setLoadingValues] = useState(false)
  const [year, setYear] = useState<number | null>(null)


  // Направления; ?direction=<code> из URL (ссылки с дашбордов направлений).
  useEffect(() => {
    fetchDirections().then((dirs) => {
      setDirections(dirs)
      const fromUrl = new URLSearchParams(window.location.search).get('direction')
      const initial = dirs.find((d) => d.code === fromUrl) ?? dirs[0]
      if (initial) setSelectedDir(initial.code)
    }).catch(() => {})
    Promise.all([fetchKatoDistricts(), fetchRegions()]).then(([kato, regs]) => {
      setKatoRegion(kato)
      setRegionNames(new Map(regs.map((r) => [r.id, r.name])))
    })
  }, [])

  // Показатели направления (без отраслевых подрядов «— …» и дат-кодов).
  useEffect(() => {
    const dir = directions.find((d) => d.code === selectedDir)
    if (!dir) return
    setYear(null)
    fetchIndicators({ direction_id: dir.id }).then((list) => {
      const usable = list
        .filter((i) => i.is_active !== false && !i.name.includes(' — ') && i.unit_name !== 'дата')
        .sort((a, b) => a.name.localeCompare(b.name, 'ru'))
      setDirIndicators(usable)
      const def = usable.find((i) => i.name === CHOROPLETH_DEFAULT[selectedDir]) ?? usable[0]
      setChIndicatorId(def?.id ?? '')
    })
  }, [selectedDir, directions])

  const presetNames = vizType === 'choropleth' ? [] : MULTI_PRESETS[selectedDir]?.[vizType] ?? []
  const selectedIndicators = useMemo<Indicator[]>(() => {
    if (vizType === 'choropleth') {
      const ind = dirIndicators.find((i) => i.id === chIndicatorId)
      return ind ? [ind] : []
    }
    return presetNames
      .map((n) => dirIndicators.find((i) => i.name === n))
      .filter((i): i is Indicator => !!i)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vizType, dirIndicators, chIndicatorId, presetNames.join('|')])

  // Догружаем значения выбранных показателей (кэш на время сессии страницы).
  useEffect(() => {
    const missing = selectedIndicators.filter((i) => !(i.id in valuesCache))
    if (missing.length === 0) return
    setLoadingValues(true)
    Promise.all(missing.map(async (i) => [i.id, await fetchAllIndicatorValues(i.id)] as const))
      .then((pairs) => setValuesCache((prev) => ({ ...prev, ...Object.fromEntries(pairs) })))
      .finally(() => setLoadingValues(false))
  }, [selectedIndicators, valuesCache])

  // Значения по районам: регион → показатель → год.
  const perRegion = useMemo(() => {
    const out = new Map<string, Map<string, Map<number, number>>>()
    for (const ind of selectedIndicators) {
      const norm = valueNormalizer(ind.name)
      for (const v of valuesCache[ind.id] ?? []) {
        if (!out.has(v.region_id)) out.set(v.region_id, new Map())
        const byInd = out.get(v.region_id)!
        if (!byInd.has(ind.id)) byInd.set(ind.id, new Map())
        byInd.get(ind.id)!.set(Number(v.period_start.slice(0, 4)), norm(Number(v.value)))
      }
    }
    return out
  }, [selectedIndicators, valuesCache])

  // Годы, за которые у ВСЕХ выбранных показателей есть данные хотя бы по половине районов.
  const availableYears = useMemo(() => {
    if (selectedIndicators.length === 0) return []
    const districtIds = Array.from(katoRegion.values())
    const years = new Set<number>()
    perRegion.forEach((byInd) => byInd.forEach((ys) => ys.forEach((_, y) => years.add(y))))
    return Array.from(years)
      .filter((y) =>
        selectedIndicators.every(
          (ind) => districtIds.filter((rid) => perRegion.get(rid)?.get(ind.id)?.has(y)).length >= districtIds.length / 2,
        ),
      )
      .sort((a, b) => b - a)
  }, [selectedIndicators, perRegion, katoRegion])

  const effectiveYear = year !== null && availableYears.includes(year) ? year : availableYears[0] ?? null

  const result = useMemo(() => {
    if (!selectedDir || Object.keys(centroids).length === 0 || effectiveYear === null) return null
    const unit = selectedIndicators[0]?.unit_name ?? ''
    const labels = selectedIndicators.map((i) => (vizType === 'choropleth' ? i.name : shortLabel(i.name)))
    const colors = selectedIndicators.map((_, k) => SERIES_COLORS[k % SERIES_COLORS.length])
    const data: DistrictData[] = []
    for (const kato of Object.keys(centroids)) {
      const rid = katoRegion.get(kato)
      if (!rid) continue
      const vals = selectedIndicators.map((ind) => perRegion.get(rid)?.get(ind.id)?.get(effectiveYear))
      if (vals.some((v) => v === undefined)) continue // район без данных — серым, без маркера
      const values = vals as number[]
      data.push({
        kato,
        district: regionNames.get(rid) ?? kato,
        mainValue: values.reduce((a, b) => a + b, 0),
        values,
        labels,
        colors,
      })
    }
    return {
      data,
      title: `${selectedIndicators.length === 1 ? selectedIndicators[0].name : directions.find((d) => d.code === selectedDir)?.name ?? ''}, ${effectiveYear}`,
      unit,
      baseColor: BASE_COLORS[selectedDir] ?? '#6366f1',
      legendLabels: labels,
      legendColors: colors,
    }
  }, [selectedDir, vizType, centroids, effectiveYear, selectedIndicators, perRegion, katoRegion, regionNames, directions])

  // Init map
  useEffect(() => {
    if (!mapContainer.current || mapRef.current) return
    let map: MapType

    async function init() {
      if (!isWebGLAvailable()) {
        setMapError(WEBGL_ERROR_MESSAGE)
        return
      }
      const maplibregl = (await import('maplibre-gl')).default
      map = new maplibregl.Map({
        container: mapContainer.current!,
        style: BASEMAP_STYLE,
        center: SKO_CENTER,
        zoom: SKO_ZOOM,
        attributionControl: false,
      })

      map.on('load', () => {
        mapRef.current = map
        // Убрать гос-/админграницы подложки — конфликтуют с границей районов.
        removeBasemapBoundaries(map)
        map.resize()
        setMapReady(true)
      })
    }

    init().catch(console.error)

    const observer = new ResizeObserver(() => mapRef.current?.resize())
    if (mapContainer.current) observer.observe(mapContainer.current)

    return () => {
      observer.disconnect()
      if (mapRef.current) { mapRef.current.remove(); mapRef.current = null }
    }
  }, [])

  // Load district boundaries
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current
    if (map.getSource('districts-src')) return

    fetchDistrictBoundaries().then((geojson) => {
      if (!mapRef.current || geojson.features.length === 0) return
      const m = mapRef.current
      if (m.getSource('districts-src')) return
      m.addSource('districts-src', { type: 'geojson', data: geojson as Parameters<GeoJSONSource['setData']>[0] })
      m.addLayer({
        id: 'districts-fill',
        type: 'fill',
        source: 'districts-src',
        paint: { 'fill-color': '#94a3b8', 'fill-opacity': 0.15 },
      })
      m.addLayer({
        id: 'districts-outline',
        type: 'line',
        source: 'districts-src',
        paint: { 'line-color': '#475569', 'line-width': 1.8, 'line-opacity': 0.7 },
      })
      // District labels
      m.addLayer({
        id: 'districts-labels',
        type: 'symbol',
        source: 'districts-src',
        layout: {
          'text-field': ['get', 'name_ru'],
          'text-font': LABEL_FONT,
          'text-size': 10,
          'text-anchor': 'center',
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': '#1e293b',
          'text-halo-color': '#ffffff',
          'text-halo-width': 1.5,
        },
      })
      // Compute centroids from actual geometry
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setCentroids(extractCentroids(geojson as any))
    }).catch(() => {})
  }, [mapReady])

  // Apply choropleth coloring on district polygons
  useEffect(() => {
    if (!mapReady || !mapRef.current || !result) return
    const map = mapRef.current

    if (vizType === 'choropleth' && map.getLayer('districts-fill') && result.data.length > 0) {
      const baseColor = result.baseColor
      // Окраска по рангу, а не по линейной шкале: г. Петропавловск по многим
      // показателям на порядок больше районов, и линейная шкала красила бы все
      // районы почти одним цветом.
      const sorted = result.data.map((d) => d.values[0]).sort((a, b) => a - b)
      const rank = (v: number) => (sorted.length > 1 ? sorted.indexOf(v) / (sorted.length - 1) : 0.5)

      const matchExpr: unknown[] = ['match', ['to-string', ['get', 'kato']]]
      result.data.forEach((d) => {
        const t = rank(d.values[0])
        const opacity = 0.15 + t * 0.7
        matchExpr.push(d.kato)
        matchExpr.push(blendColor(baseColor, opacity))
      })
      matchExpr.push('#e2e8f0') // fallback

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      map.setPaintProperty('districts-fill', 'fill-color', matchExpr as any)
      map.setPaintProperty('districts-fill', 'fill-opacity', 0.85)
    } else if (map.getLayer('districts-fill')) {
      map.setPaintProperty('districts-fill', 'fill-color', '#94a3b8')
      map.setPaintProperty('districts-fill', 'fill-opacity', 0.15)
    }
  }, [mapReady, vizType, result])

  // Render chart markers for non-choropleth types
  useEffect(() => {
    // Clear old markers
    markersRef.current.forEach((m) => m.remove())
    markersRef.current = []

    if (!mapReady || !mapRef.current || !result || vizType === 'choropleth') return

    async function addMarkers() {
      const maplibregl = (await import('maplibre-gl')).default
      const map = mapRef.current!
      const chartSize = vizType === 'prop-pie' ? 0 : 56

      result!.data.forEach((d) => {
        const coords = centroids[d.kato]
        if (!coords) return

        let svgHtml = ''
        let size = chartSize

        if (vizType === 'vbar') {
          svgHtml = renderVerticalBar(d.values, d.colors, 56)
          size = 56
        } else if (vizType === 'hbar') {
          svgHtml = renderHorizontalBar(d.values, d.colors, 56)
          size = 56
        } else if (vizType === 'pie') {
          svgHtml = renderPie(d.values, d.colors, 48)
          size = 48
        } else if (vizType === 'prop-pie') {
          // Radius proportional to mainValue
          const allMain = result!.data.map((x) => x.mainValue)
          const maxMain = Math.max(...allMain, 1)
          const minR = 20
          const maxR = 44
          const r = minR + (d.mainValue / maxMain) * (maxR - minR)
          svgHtml = renderPie(d.values, d.colors, r * 2)
          size = r * 2
        }

        const el = document.createElement('div')
        el.style.width = `${size}px`
        el.style.height = `${size}px`
        el.style.cursor = 'pointer'
        el.style.filter = 'drop-shadow(0 1px 3px rgba(0,0,0,0.25))'
        el.innerHTML = sanitizeSvg(svgHtml)

        el.addEventListener('mouseenter', (ev) => {
          const rect = mapContainer.current!.getBoundingClientRect()
          setTooltip({
            x: (ev as MouseEvent).clientX - rect.left,
            y: (ev as MouseEvent).clientY - rect.top,
            district: d.district,
            values: d.values,
            labels: d.labels,
            unit: result!.unit,
          })
        })
        el.addEventListener('mouseleave', () => setTooltip(null))

        const marker = new maplibregl.Marker({ element: el, anchor: 'center' })
          .setLngLat(coords as LngLatLike)
          .addTo(map)
        markersRef.current.push(marker)
      })
    }

    addMarkers().catch(() => {})
  }, [mapReady, vizType, result, centroids])

  // Choropleth tooltip via mousemove on districts
  useEffect(() => {
    if (!mapReady || !mapRef.current || vizType !== 'choropleth' || !result) return
    const map = mapRef.current

    function onMove(e: import('maplibre-gl').MapMouseEvent) {
      const features = map.queryRenderedFeatures(e.point, { layers: ['districts-fill'] })
      if (features.length > 0) {
        const kato = String(features[0].properties?.kato ?? '')
        const d = result!.data.find((x) => x.kato === kato)
        if (d) {
          map.getCanvas().style.cursor = 'pointer'
          setTooltip({
            x: e.point.x,
            y: e.point.y,
            district: d.district,
            values: d.values,
            labels: d.labels,
            unit: result!.unit,
          })
          return
        }
      }
      map.getCanvas().style.cursor = ''
      setTooltip(null)
    }

    map.on('mousemove', onMove)
    return () => { map.off('mousemove', onMove) }
  }, [mapReady, vizType, result])

  const selectedDirName = directions.find((d) => d.code === selectedDir)?.name ?? ''

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ position: 'relative', flex: '1 1 0', minHeight: 0, overflow: 'hidden' }}>
        {/* Map */}
        <div ref={mapContainer} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
          <div
            className={`flex h-full items-center justify-center px-6 text-center text-sm ${
              mapError ? 'bg-red-50 text-red-700' : 'bg-gray-100 text-gray-400'
            }`}
          >
            <span className="max-w-md">{mapError ?? 'Загрузка карты...'}</span>
          </div>
        </div>

        {/* Controls panel (top-left) */}
        <div className="absolute left-3 top-3 z-10 w-72 rounded-xl bg-white shadow-lg ring-1 ring-gray-200">
          <div className="border-b border-gray-100 px-4 py-3">
            <h3 className="text-sm font-semibold text-gray-900">Направление</h3>
          </div>
          <div className="p-3 space-y-3">
            <select
              value={selectedDir}
              onChange={(e) => setSelectedDir(e.target.value)}
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              {directions.map((d) => (
                <option key={d.code} value={d.code}>{d.name}</option>
              ))}
            </select>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Тип визуализации</label>
              <select
                value={vizType}
                onChange={(e) => setVizType(e.target.value as VizType)}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              >
                {VIZ_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            {vizType === 'choropleth' ? (
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Показатель</label>
                <select
                  value={chIndicatorId}
                  onChange={(e) => setChIndicatorId(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                >
                  {dirIndicators.map((i) => (
                    <option key={i.id} value={i.id}>{i.name}</option>
                  ))}
                </select>
              </div>
            ) : (
              selectedIndicators.length === 0 && (
                <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
                  Для этого направления нет набора показателей одной единицы для такой диаграммы — выберите картограмму.
                </p>
              )
            )}
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Год</label>
              <select
                value={effectiveYear ?? ''}
                onChange={(e) => setYear(Number(e.target.value))}
                disabled={availableYears.length === 0}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-gray-50"
              >
                {availableYears.length === 0 && <option value="">—</option>}
                {availableYears.map((y) => (
                  <option key={y} value={y}>{y}</option>
                ))}
              </select>
            </div>
            {loadingValues ? (
              <p className="text-xs text-gray-400">Загрузка данных…</p>
            ) : (
              selectedIndicators.length > 0 && availableYears.length === 0 && (
                <p className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-500">Нет данных по районам для выбранного показателя</p>
              )
            )}
            </div>
          </div>
        </div>

        {/* Legend panel (bottom-left) */}
        {result && result.data.length > 0 && (
          <div className="absolute left-3 bottom-3 z-10 w-72 rounded-xl bg-white shadow-lg ring-1 ring-gray-200">
            <div className="border-b border-gray-100 px-4 py-2.5">
              <h3 className="text-xs font-semibold text-gray-900">
                Легенда — {result.title}
              </h3>
              <p className="text-xs text-gray-400">{result.unit}</p>
            </div>
            <div className="p-3">
              {vizType === 'choropleth' ? (
                <ChoroplethLegend
                  data={result.data}
                  baseColor={result.baseColor}
                  unit={result.unit}
                  label={result.legendLabels[0]}
                />
              ) : (
                <div className="space-y-1.5">
                  {result.legendLabels.map((label, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <span
                        className="h-3 w-3 rounded-sm flex-shrink-0"
                        style={{ backgroundColor: result.legendColors[i] }}
                      />
                      <span className="text-xs text-gray-700">{label}</span>
                    </div>
                  ))}
                  {vizType === 'prop-pie' && (
                    <div className="mt-2 pt-2 border-t border-gray-100">
                      <div className="text-xs text-gray-500 mb-1">Размер пирога = суммарный показатель</div>
                      <div className="flex items-end gap-2">
                        <div className="w-4 h-4 rounded-full border border-gray-300" />
                        <span className="text-xs text-gray-400">мин.</span>
                        <div className="w-8 h-8 rounded-full border border-gray-300" />
                        <span className="text-xs text-gray-400">макс.</span>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Zoom controls (top-right) */}
        <div className="absolute right-3 top-3 z-10 flex flex-col gap-1">
          {[
            { label: '+', fn: () => mapRef.current?.zoomIn() },
            { label: '\u2212', fn: () => mapRef.current?.zoomOut() },
            { label: '\u2316', fn: () => mapRef.current?.flyTo({ center: SKO_CENTER, zoom: SKO_ZOOM }) },
          ].map((c) => (
            <button
              key={c.label}
              onClick={c.fn}
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-white text-lg text-gray-700 shadow-lg ring-1 ring-gray-200 hover:bg-gray-50"
            >
              {c.label}
            </button>
          ))}
        </div>

        {/* Title badge (top-center) */}
        {selectedDirName && (
          <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 rounded-lg bg-white/90 backdrop-blur-sm px-4 py-2 shadow-md ring-1 ring-gray-200">
            <span className="text-sm font-semibold text-gray-900">{selectedDirName}</span>
            <span className="ml-2 text-xs text-gray-500">{VIZ_OPTIONS.find((o) => o.value === vizType)?.label}</span>
          </div>
        )}

        {/* Tooltip */}
        {tooltip && (
          <div
            className="pointer-events-none absolute z-30 max-w-[260px] rounded-lg bg-gray-900/90 px-3 py-2 text-xs text-white shadow-xl backdrop-blur-sm"
            style={{ left: tooltip.x + 14, top: tooltip.y - 10 }}
          >
            <div className="font-medium text-gray-100 mb-1">{tooltip.district}</div>
            {tooltip.values.map((v, i) => (
              <div key={i} className="flex justify-between gap-3">
                <span className="text-gray-400">{tooltip.labels[i]}</span>
                <span className="font-medium">{formatNumber(v, 2)} {tooltip.unit}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Choropleth Legend ────────────────────────────────────────────────

function ChoroplethLegend({ data, baseColor, unit, label }: {
  data: DistrictData[]
  baseColor: string
  unit: string
  label: string
}) {
  const vals = data.map((d) => d.values[0])
  const minVal = Math.min(...vals)
  const maxVal = Math.max(...vals)

  return (
    <div>
      <div className="text-xs text-gray-600 mb-2">{label}</div>
      <div
        className="h-4 w-full rounded"
        style={{
          background: `linear-gradient(to right, ${blendColor(baseColor, 0.15)}, ${blendColor(baseColor, 0.85)})`,
        }}
      />
      <div className="flex justify-between mt-1 text-xs text-gray-500">
        <span>мин. {formatNumber(minVal, 2)} {unit}</span>
        <span>макс. {formatNumber(maxVal, 2)} {unit}</span>
      </div>
      <div className="mt-1 text-[11px] text-gray-400">Цвет — место района в рейтинге</div>
    </div>
  )
}

// ─── Color helpers ───────────────────────────────────────────────────

function blendColor(hex: string, t: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  // Blend toward white (255) for low t, toward full color for high t
  const mix = (c: number) => Math.round(255 - t * (255 - c))
  return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`
}
