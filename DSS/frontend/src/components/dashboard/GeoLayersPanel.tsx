'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Map as MapType, Popup as PopupType } from 'maplibre-gl'
import { BASEMAP_STYLE, removeBasemapBoundaries } from '@/lib/map/basemap'
import { isWebGLAvailable, WEBGL_ERROR_MESSAGE } from '@/lib/webgl'
import { fetchDistrictBoundaries } from '@/lib/api/backend'
import { apiErrorMessage } from '@/lib/api/landscape'
import {
  fetchGeoLayerGeojson,
  fetchGeoLayers,
  fetchGeoLayerSld,
  fetchGeoRasterPoint,
  fetchGeoRasterStats,
  geoTileTemplate,
  waitGeoRaster,
  type GeoFeatureCollection,
  type GeoLayerInfo,
} from '@/lib/api/dashboardGeo'
import { parseRasterColorMap, parseSld, rasterColorMapToTitiler, type MlExpr } from '@/lib/gis/sld'
import { withApiCredentials } from '@/lib/session'

/**
 * Блок дашборда «карта из каталога геоданных».
 *
 * Карта создаётся, только когда блок попадает в область видимости. Одновременно
 * показывается не больше одного растра (поверхности) и любое число векторных
 * слоёв поверх него. Слой подготавливается при первом включении и дальше лишь
 * скрывается/показывается — повторное включение мгновенное.
 *
 * Стиль берётся из SLD каталога. Если SLD нет или он ссылается на атрибуты,
 * которых в наборе нет, слой рисуется стилем по умолчанию; для точек с
 * category_prop — цветом по значению атрибута.
 */

const SKO_CENTER: [number, number] = [69.09, 54.5]
const SKO_ZOOM = 6.2
const DISTRICTS = 'geo-districts-line'
const MAX_DISCRETE = 12

const CATEGORY_PALETTE = [
  '#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4',
  '#42d4f4', '#f032e6', '#9a6324', '#469990', '#800000', '#808000', '#000075',
]
/** Цвета классов растра без стиля, когда смысл градаций в каталоге не описан:
 * светлая палитра без порядка «лучше — хуже», чтобы не подсказывать оценку. */
const NEUTRAL_CLASSES = ['#8dd3c7', '#ffffb3', '#bebada', '#fb8072', '#80b1d3', '#fdb462', '#b3de69', '#fccde5']

/** Опорные цвета непрерывных шкал titiler (для полоски легенды). */
const GRADIENTS: Record<string, string[]> = {
  viridis: ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'],
  ylorrd: ['#ffffcc', '#fed976', '#fd8d3c', '#e31a1c', '#800026'],
  ylorbr: ['#ffffe5', '#fee391', '#fe9929', '#cc4c02', '#662506'],
  rdylgn: ['#a50026', '#f46d43', '#ffffbf', '#66bd63', '#006837'],
}

interface LegendItem {
  color: string
  label: string
  kind: 'polygon' | 'line' | 'point' | 'raster'
}

interface Prepared {
  legend: LegendItem[]
  gradient?: { min: number; max: number; colors: string[] }
  note?: string
  /** Для растра: классы по значению (подсказка по щелчку). */
  classLabels?: Map<number, string>
}

type Phase = 'idle' | 'loading' | 'ready' | 'error'

interface LayerState {
  phase: Phase
  error?: string
  progress?: number | null
  prepared?: Prepared
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}

function fmt(v: number): string {
  const a = Math.abs(v)
  if (a !== 0 && (a < 0.01 || a >= 100000)) return v.toExponential(2).replace('.', ',')
  return v.toLocaleString('ru-RU', { maximumFractionDigits: a < 1 ? 4 : 2 })
}

function hexToRgb(h: string): [number, number, number] {
  const v = h.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)) as [number, number, number]
}

function sampleRamp(stops: string[], n: number): string[] {
  if (n <= 1) return [stops[stops.length - 1]]
  if (n === stops.length) return stops
  const rgb = stops.map(hexToRgb)
  return Array.from({ length: n }, (_, i) => {
    const t = (i / (n - 1)) * (rgb.length - 1)
    const k = Math.min(Math.floor(t), rgb.length - 2)
    const f = t - k
    const c = rgb[k].map((x, j) => Math.round(x + (rgb[k + 1][j] - x) * f))
    return `#${c.map((x) => x.toString(16).padStart(2, '0')).join('')}`
  })
}

function geometryKinds(fc: GeoFeatureCollection): Set<'polygon' | 'line' | 'point'> {
  const out = new Set<'polygon' | 'line' | 'point'>()
  for (const f of fc.features) {
    const t: string = f?.geometry?.type ?? ''
    if (t.includes('Polygon')) out.add('polygon')
    else if (t.includes('Line')) out.add('line')
    else if (t.includes('Point')) out.add('point')
  }
  return out
}

function layerIds(key: string) {
  return {
    src: `geo-src-${key}`,
    fill: `geo-${key}-fill`,
    outline: `geo-${key}-outline`,
    line: `geo-${key}-line`,
    circle: `geo-${key}-circle`,
    raster: `geo-${key}-raster`,
  }
}

function periodLabel(info: GeoLayerInfo): string | null {
  const y1 = info.start_date?.slice(0, 4)
  const y2 = info.end_date?.slice(0, 4)
  if (y1 && y2 && y1 !== y2) return `${y1}–${y2}`
  return y2 || y1 || null
}

export interface GeoLayersPanelProps {
  title: string
  description?: string
  /** Ключи слоёв из перечня /dashboard-geo (порядок кнопок). */
  layers: string[]
  /** Включённые при открытии; по умолчанию — первый слой. */
  initial?: string[]
  height?: number
}

export default function GeoLayersPanel({ title, description, layers, initial, height = 460 }: GeoLayersPanelProps) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapType | null>(null)
  const popupRef = useRef<PopupType | null>(null)
  const addedRef = useRef<Set<string>>(new Set())
  const statesRef = useRef<Record<string, LayerState>>({})
  const activeRef = useRef<string[]>([])
  const infosRef = useRef<Record<string, GeoLayerInfo>>({})

  const [visible, setVisible] = useState(false)
  const [mapReady, setMapReady] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)
  const [infos, setInfos] = useState<GeoLayerInfo[] | null>(null)
  const [infoError, setInfoError] = useState<string | null>(null)
  const [active, setActive] = useState<string[]>(() => initial ?? layers.slice(0, 1))
  const [states, setStates] = useState<Record<string, LayerState>>({})
  const [retry, setRetry] = useState(0)

  activeRef.current = active

  const setLayerState = useCallback((key: string, st: LayerState) => {
    statesRef.current = { ...statesRef.current, [key]: st }
    setStates(statesRef.current)
  }, [])

  // ── Ленивая инициализация: только когда блок виден ─────────────────────────
  useEffect(() => {
    const el = wrapRef.current
    if (!el || visible) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true)
          io.disconnect()
        }
      },
      { rootMargin: '200px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [visible])

  // ── Описание слоёв ─────────────────────────────────────────────────────────
  const layersKey = layers.join(',')
  useEffect(() => {
    if (!visible) return
    let cancelled = false
    setInfoError(null)
    fetchGeoLayers(layers)
      .then((list) => {
        if (cancelled) return
        infosRef.current = Object.fromEntries(list.map((i) => [i.key, i]))
        setInfos(list)
      })
      .catch((e) => !cancelled && setInfoError(apiErrorMessage(e, 'Каталог геоданных недоступен')))
    return () => {
      cancelled = true
    }
  }, [visible, layersKey, retry]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Карта (один раз, после появления в области видимости) ──────────────────
  useEffect(() => {
    if (!visible) return
    let cancelled = false
    let map: MapType | undefined

    async function init() {
      if (!isWebGLAvailable()) {
        setMapError(WEBGL_ERROR_MESSAGE)
        return
      }
      const maplibregl = (await import('maplibre-gl')).default
      if (cancelled || !container.current) return
      const m = new maplibregl.Map({
        container: container.current,
        style: BASEMAP_STYLE,
        center: SKO_CENTER,
        zoom: SKO_ZOOM,
        attributionControl: false,
        // Тайлы грузит сам maplibre (вне axios): запросам к API нужны cookie сеанса.
        transformRequest: withApiCredentials,
      })
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left')
      m.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left')
      popupRef.current = new maplibregl.Popup({ closeButton: true, closeOnClick: true, maxWidth: '280px' })
      map = m

      m.on('load', async () => {
        if (cancelled) return
        removeBasemapBoundaries(m)
        const districts = await fetchDistrictBoundaries()
        if (cancelled) return
        m.addSource('geo-districts', { type: 'geojson', data: districts as never })
        m.addLayer({
          id: DISTRICTS,
          type: 'line',
          source: 'geo-districts',
          paint: { 'line-color': '#334155', 'line-width': 0.9, 'line-opacity': 0.7 },
        })
        try {
          const b = new maplibregl.LngLatBounds()
          const extend = (c: unknown) => {
            if (typeof (c as number[])[0] === 'number') b.extend(c as [number, number])
            else if (Array.isArray(c)) c.forEach(extend)
          }
          districts.features.forEach((f) => extend((f.geometry as { coordinates?: unknown })?.coordinates))
          if (!b.isEmpty()) {
            m.resize()
            m.fitBounds(b, { padding: 20, duration: 0 })
          }
        } catch {
          /* охват — по возможности */
        }

        m.on('click', async (e) => {
          const act = activeRef.current
          // Сначала векторные объекты под курсором (верхний слой — первый).
          const vectorLayerIds = act
            .filter((k) => infosRef.current[k]?.kind === 'vector' && addedRef.current.has(k))
            .flatMap((k) => {
              const ids = layerIds(k)
              return [ids.circle, ids.line, ids.fill].filter((id) => m.getLayer(id))
            })
          const hits = vectorLayerIds.length ? m.queryRenderedFeatures(e.point, { layers: vectorLayerIds }) : []
          if (hits.length) {
            const f = hits[0]
            const key = act.find((k) => f.layer.id.startsWith(`geo-${k}-`))
            const info = key ? infosRef.current[key] : undefined
            if (!info) return
            const rows = (info.popup.length ? info.popup : Object.keys(f.properties ?? {}).slice(0, 3))
              .map((p) => f.properties?.[p])
              .filter((v) => v != null && v !== '')
              .map((v) => escapeHtml(String(v)))
            popupRef.current
              ?.setLngLat(e.lngLat)
              .setHTML(`<div style="font-size:12px"><b>${escapeHtml(info.title)}</b>${rows.length ? '<br/>' + rows.join('<br/>') : ''}</div>`)
              .addTo(m)
            return
          }
          const rasterKey = act.find((k) => infosRef.current[k]?.kind === 'raster' && addedRef.current.has(k))
          if (!rasterKey) return
          const info = infosRef.current[rasterKey]
          const val = await fetchGeoRasterPoint(rasterKey, e.lngLat.lng, e.lngLat.lat)
          if (val == null || cancelled) return
          const label = statesRef.current[rasterKey]?.prepared?.classLabels?.get(val)
          const text = label ? label : `${fmt(val)}${info.unit ? ' ' + info.unit : ''}`
          popupRef.current
            ?.setLngLat(e.lngLat)
            .setHTML(`<div style="font-size:12px"><b>${escapeHtml(info.title)}</b><br/>${escapeHtml(text)}</div>`)
            .addTo(m)
        })

        mapRef.current = m
        m.resize()
        setMapReady(true)
      })
    }

    init().catch((e) => setMapError(String(e)))
    const observer = new ResizeObserver(() => mapRef.current?.resize())
    if (container.current) observer.observe(container.current)
    return () => {
      cancelled = true
      observer.disconnect()
      popupRef.current?.remove()
      map?.remove()
      mapRef.current = null
      addedRef.current = new Set()
      setMapReady(false)
    }
  }, [visible])

  // ── Подготовка и показ слоёв ───────────────────────────────────────────────
  const addVector = useCallback(async (m: MapType, info: GeoLayerInfo): Promise<Prepared> => {
    const [fc, sldXml] = await Promise.all([
      fetchGeoLayerGeojson(info.key),
      info.has_sld ? fetchGeoLayerSld(info.key) : Promise.resolve(null),
    ])
    const ids = layerIds(info.key)
    const kinds = geometryKinds(fc)
    const props = new Set<string>(Object.keys(fc.features[0]?.properties ?? {}))
    let sld = null
    try {
      sld = sldXml ? parseSld(sldXml) : null
    } catch {
      sld = null
    }
    // Стиль, ссылающийся на отсутствующие в наборе атрибуты, не применяем:
    // объекты без совпавшего правила стали бы невидимыми.
    if (sld && !sld.propertyNames.every((p) => props.has(p))) sld = null

    let legend: LegendItem[] = []
    let categoryColor: MlExpr | null = null
    if (!sld && info.category_prop) {
      const prop = info.category_prop
      const values = Array.from(new Set(fc.features.map((f) => f.properties?.[prop]).filter((v) => v != null))).map(String).sort()
      const colors = values.map((_, i) => CATEGORY_PALETTE[i % CATEGORY_PALETTE.length])
      categoryColor = values.length
        ? ['match', ['to-string', ['get', prop]], ...values.flatMap((v, i) => [v, colors[i]]), '#64748b']
        : '#2563eb'
      const kind = kinds.has('point') ? 'point' : kinds.has('line') ? 'line' : 'polygon'
      legend = values.map((v, i) => ({ color: colors[i], label: v, kind }))
    }

    m.addSource(ids.src, { type: 'geojson', data: fc as never })
    if (kinds.has('polygon')) {
      m.addLayer({
        id: ids.fill,
        type: 'fill',
        source: ids.src,
        filter: ['in', ['geometry-type'], ['literal', ['Polygon', 'MultiPolygon']]],
        paint: {
          'fill-color': sld?.fill?.color ?? categoryColor ?? '#3b82f6',
          'fill-opacity': sld?.fill ? sld.fill.opacity : 0.45,
        },
      })
      m.addLayer({
        id: ids.outline,
        type: 'line',
        source: ids.src,
        filter: ['in', ['geometry-type'], ['literal', ['Polygon', 'MultiPolygon']]],
        paint: {
          'line-color': sld?.outline?.color ?? '#1e3a8a',
          'line-width': sld?.outline?.width ?? 0.6,
        },
      })
    }
    if (kinds.has('line')) {
      m.addLayer({
        id: ids.line,
        type: 'line',
        source: ids.src,
        filter: ['in', ['geometry-type'], ['literal', ['LineString', 'MultiLineString']]],
        paint: {
          'line-color': sld?.line?.color ?? categoryColor ?? '#2563eb',
          'line-width': sld?.line?.width ?? 1.4,
        },
      })
    }
    if (kinds.has('point')) {
      m.addLayer({
        id: ids.circle,
        type: 'circle',
        source: ids.src,
        filter: ['in', ['geometry-type'], ['literal', ['Point', 'MultiPoint']]],
        paint: {
          'circle-color': sld?.circle?.color ?? categoryColor ?? '#dc2626',
          'circle-radius': sld?.circle?.radius ?? 5,
          'circle-stroke-color': sld?.circle?.strokeColor ?? '#ffffff',
          'circle-stroke-width': sld?.circle?.strokeWidth ?? 1.2,
        },
      })
    }
    if (sld) {
      // Подписи правил из QGIS бывают служебными: «Single symbol» или голый код категории.
      legend = sld.legend.map((l) => {
        const raw = (l.label ?? '').trim()
        const label =
          !raw || raw === '—' || /^single symbol$/i.test(raw) ? info.title : /^\d+$/.test(raw) ? `категория ${raw}` : raw
        return { color: l.color, label, kind: l.kind }
      })
    } else if (!legend.length) {
      const kind = kinds.has('polygon') ? 'polygon' : kinds.has('line') ? 'line' : 'point'
      legend = [{ color: kind === 'point' ? '#dc2626' : kind === 'line' ? '#2563eb' : '#3b82f6', label: info.title, kind }]
    }
    return { legend }
  }, [])

  const addRaster = useCallback(
    async (m: MapType, info: GeoLayerInfo, stale: () => boolean): Promise<Prepared | null> => {
      await waitGeoRaster(info.key, stale, (share) =>
        setLayerState(info.key, { phase: 'loading', progress: share }),
      )
      if (stale()) return null
      const ids = layerIds(info.key)
      let tiles: string | null = null
      let prepared: Prepared | null = null

      const xml = info.has_sld ? await fetchGeoLayerSld(info.key) : null
      const cm = xml ? parseRasterColorMap(xml) : null
      if (cm) {
        tiles = geoTileTemplate(info.key, { colormap: rasterColorMapToTitiler(cm) })
        prepared = {
          legend: cm.entries.map((e) => ({ color: e.color, label: e.label || String(e.quantity), kind: 'raster' as const })),
          classLabels: new Map(cm.entries.map((e) => [e.quantity, e.label || String(e.quantity)])),
        }
      } else {
        const st = await fetchGeoRasterStats(info.key)
        const count = st.max - st.min + 1
        if (st.integer && count <= MAX_DISCRETE) {
          const colors = info.ramp.length ? sampleRamp(info.ramp, count) : NEUTRAL_CLASSES.slice(0, count)
          const values = Array.from({ length: count }, (_, i) => st.min + i)
          tiles = geoTileTemplate(info.key, {
            colormap: Object.fromEntries(values.map((v, i) => [String(v), colors[i]])),
          })
          const known = info.class_labels.length === count
          const label = (v: number, i: number) => (known ? `${v} — ${info.class_labels[i]}` : `класс ${v}`)
          prepared = {
            legend: values.map((v, i) => ({ color: colors[i], label: label(v, i), kind: 'raster' as const })),
            note: known ? undefined : 'Градации исходной карты; их описание в каталоге не задано.',
            classLabels: new Map(values.map((v, i) => [v, label(v, i)])),
          }
        } else {
          const name = GRADIENTS[info.colormap_name] ? info.colormap_name : 'viridis'
          // Шкала по 2–98-му процентилям: единичные выбросы не «съедают» палитру.
          tiles = geoTileTemplate(info.key, { colormapName: name, rescale: `${st.p2},${st.p98}` })
          prepared = { legend: [], gradient: { min: st.p2, max: st.p98, colors: GRADIENTS[name] } }
        }
      }
      if (stale()) return null
      m.addSource(ids.src, { type: 'raster', tiles: [tiles], tileSize: 256 })
      // Растр — под границами районов и векторными слоями.
      m.addLayer(
        { id: ids.raster, type: 'raster', source: ids.src, paint: { 'raster-opacity': 0.85, 'raster-resampling': 'nearest' } },
        m.getLayer(DISTRICTS) ? DISTRICTS : undefined,
      )
      return prepared
    },
    [setLayerState],
  )

  const setVisibility = useCallback((m: MapType, key: string, on: boolean) => {
    const ids = layerIds(key)
    for (const id of [ids.fill, ids.outline, ids.line, ids.circle, ids.raster]) {
      if (m.getLayer(id)) m.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none')
    }
  }, [])

  useEffect(() => {
    const m = mapRef.current
    if (!mapReady || !m || !infos) return
    for (const info of infos) {
      const on = active.includes(info.key)
      if (addedRef.current.has(info.key)) {
        setVisibility(m, info.key, on)
        continue
      }
      if (!on || info.error) continue
      const cur = statesRef.current[info.key]
      if (cur?.phase === 'loading' || cur?.phase === 'error') continue
      setLayerState(info.key, { phase: 'loading' })
      // Загрузка не прерывается переключением кнопок: если слой успели выключить,
      // он добавится скрытым. Прерывает её только пересоздание карты.
      const stale = () => mapRef.current !== m
      ;(async () => {
        try {
          const prepared = info.kind === 'vector' ? await addVector(m, info) : await addRaster(m, info, stale)
          if (stale() || !prepared) return
          addedRef.current.add(info.key)
          setVisibility(m, info.key, activeRef.current.includes(info.key))
          setLayerState(info.key, { phase: 'ready', prepared })
        } catch (e) {
          if (stale()) return
          setLayerState(info.key, {
            phase: 'error',
            error: e instanceof Error && !('response' in e) ? e.message : apiErrorMessage(e, 'Не удалось показать слой'),
          })
        }
      })()
    }
  }, [mapReady, infos, active, addVector, addRaster, setVisibility, setLayerState])

  const toggle = useCallback(
    (key: string) => {
      popupRef.current?.remove()
      const info = infosRef.current[key]
      setActive((cur) => {
        if (cur.includes(key)) return cur.filter((k) => k !== key)
        // Растр одновременно один: включение другого выключает прежний.
        const base = info?.kind === 'raster' ? cur.filter((k) => infosRef.current[k]?.kind !== 'raster') : cur
        return [...base, key]
      })
      if (statesRef.current[key]?.phase === 'error') {
        const { [key]: _drop, ...rest } = statesRef.current // eslint-disable-line @typescript-eslint/no-unused-vars
        statesRef.current = rest
        setStates(rest)
      }
    },
    [],
  )

  const byKey = useMemo(() => Object.fromEntries((infos ?? []).map((i) => [i.key, i])), [infos])
  const shown = active.filter((k) => byKey[k] && !byKey[k].error)
  const loading = shown.filter((k) => states[k]?.phase === 'loading')
  const errors = shown.filter((k) => states[k]?.phase === 'error')

  return (
    <section ref={wrapRef} className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3">
        <h3 className="text-lg font-semibold text-gray-900">{title}</h3>
        {description && <p className="mt-0.5 text-sm text-gray-500">{description}</p>}
      </div>

      {layers.length > 1 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {(infos ?? layers.map((k) => ({ key: k, title: '…', kind: 'vector' }) as GeoLayerInfo)).map((info) => {
            const on = active.includes(info.key)
            const disabled = !!info.error
            return (
              <button
                key={info.key}
                type="button"
                disabled={disabled}
                title={info.error ?? info.description ?? undefined}
                onClick={() => toggle(info.key)}
                className={`rounded-full border px-3 py-1 text-sm transition-colors ${
                  disabled
                    ? 'cursor-not-allowed border-gray-200 text-gray-300'
                    : on
                      ? 'border-primary-600 bg-primary-50 font-medium text-primary-700'
                      : 'border-gray-300 text-gray-600 hover:bg-gray-50'
                }`}
              >
                {info.kind === 'raster' ? '▦ ' : '◇ '}
                {info.title}
              </button>
            )
          })}
        </div>
      )}

      <div className="relative w-full overflow-hidden rounded-lg border border-gray-200" style={{ height }}>
        {mapError ? (
          <div className="flex h-full items-center justify-center bg-gray-50 p-4 text-center text-sm text-gray-500">{mapError}</div>
        ) : (
          <div className="absolute inset-0">
            <div ref={container} className="h-full w-full" />
          </div>
        )}

        {!mapError && (!visible || !mapReady) && (
          <div className="absolute inset-0 animate-pulse bg-gray-100" />
        )}

        {infoError && (
          <div className="absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-xs text-rose-800 shadow">
            <span>{infoError}</span>
            <button onClick={() => setRetry((r) => r + 1)} className="font-medium underline">
              Повторить
            </button>
          </div>
        )}

        {loading.length > 0 && (
          <div className="absolute left-1/2 top-3 -translate-x-1/2 rounded-full bg-white/95 px-4 py-1.5 text-xs text-gray-600 shadow">
            Подготовка слоя к отображению
            {loading
              .map((k) => states[k]?.progress)
              .filter((p): p is number => typeof p === 'number')
              .map((p) => ` · ${Math.round(p * 100)} %`)
              .join('')}
            …
          </div>
        )}

        {errors.map((k) => (
          <div
            key={k}
            className="absolute left-1/2 top-12 flex -translate-x-1/2 items-center gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-xs text-rose-800 shadow"
          >
            <span>
              {byKey[k]?.title}: {states[k]?.error}
            </span>
            <button onClick={() => toggle(k)} className="font-medium underline">
              Скрыть
            </button>
          </div>
        ))}

        {shown.some((k) => states[k]?.phase === 'ready') && (
          <div className="absolute right-3 top-3 max-h-[calc(100%-24px)] max-w-[270px] space-y-3 overflow-y-auto rounded-lg bg-white/95 p-3 text-xs shadow">
            {shown
              .filter((k) => states[k]?.phase === 'ready')
              .map((k) => {
                const p = states[k]!.prepared!
                const info = byKey[k]
                const single = p.legend.length === 1 && p.legend[0].label === info.title
                return (
                  <div key={k}>
                    {!single && <div className="mb-1.5 font-semibold text-gray-900">{info.title}</div>}
                    {p.legend.length > 0 && (
                      <ul className="space-y-1">
                        {p.legend.map((l, i) => (
                          <li key={i} className="flex items-start gap-2">
                            <span
                              className={`mt-0.5 shrink-0 border border-black/10 ${
                                l.kind === 'point' ? 'h-3 w-3 rounded-full' : l.kind === 'line' ? 'h-1 w-4 self-center' : 'h-3 w-3 rounded-sm'
                              }`}
                              style={{ background: l.color }}
                            />
                            <span className={single ? 'font-medium text-gray-900' : 'text-gray-700'}>{l.label}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    {p.gradient && (
                      <div>
                        <div
                          className="h-2.5 rounded"
                          style={{ background: `linear-gradient(to right, ${p.gradient.colors.join(', ')})` }}
                        />
                        <div className="mt-1 flex justify-between text-gray-500">
                          <span>≤ {fmt(p.gradient.min)}</span>
                          <span>≥ {fmt(p.gradient.max)}</span>
                        </div>
                        {info.unit && <div className="text-gray-400">{info.unit}</div>}
                      </div>
                    )}
                    {p.note && <p className="mt-1 text-[11px] leading-snug text-gray-400">{p.note}</p>}
                  </div>
                )
              })}
          </div>
        )}
      </div>

      {shown.length > 0 && (
        <p className="mt-2 text-xs text-gray-400">
          Источник — каталог геоданных:{' '}
          {shown
            .map((k) => {
              const i = byKey[k]
              const period = periodLabel(i)
              return `${i.title}${period ? ` (${period})` : ''}`
            })
            .join('; ')}
          . Щёлкните по карте, чтобы увидеть значение.
        </p>
      )}
    </section>
  )
}
