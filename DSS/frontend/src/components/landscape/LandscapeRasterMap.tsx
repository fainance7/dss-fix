'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Map as MapType, Popup as PopupType } from 'maplibre-gl'
import { BASEMAP_STYLE, removeBasemapBoundaries } from '@/lib/map/basemap'
import { isWebGLAvailable, WEBGL_ERROR_MESSAGE } from '@/lib/webgl'
import { fetchDistrictBoundaries } from '@/lib/api/backend'
import {
  apiErrorMessage,
  fetchLandscapeSld,
  landscapeRasterPoint,
  landscapeRasterStatistics,
  landscapeTileTemplate,
  waitRasterIngest,
} from '@/lib/api/landscape'
import { parseRasterColorMap, rasterColorMapToTitiler } from '@/lib/gis/sld'
import { withApiCredentials } from '@/lib/session'

const SKO_CENTER: [number, number] = [69.09, 54.5]
const SKO_ZOOM = 6.2
const SRC = 'lp-raster'
const LAYER = 'lp-raster-layer'
/** Больше классов — непрерывная шкала вместо дискретной легенды. */
const MAX_DISCRETE = 12

export interface LandscapeMapLayer {
  datasetId: string
  title: string
  /** sld_url из /landscape/results или /datasets/{id}/sld входа; нет SLD — шкала по данным. */
  sldUrl?: string | null
  /** Цвета шкалы для слоёв без SLD (светлый → тёмный). */
  ramp: string[]
}

interface LegendItem {
  value: number
  color: string
  label: string
}

interface PreparedStyle {
  tiles: string
  legend: LegendItem[]
  /** Стиль из ГИС (SLD) или построен по данным. */
  fromSld: boolean
  gradient?: { min: number; max: number; colors: string[] }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}

function hexToRgb(h: string): [number, number, number] {
  const v = h.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)) as [number, number, number]
}

function rgbToHex([r, g, b]: number[]): string {
  return `#${[r, g, b].map((c) => Math.round(c).toString(16).padStart(2, '0')).join('')}`
}

/** n цветов, равномерно взятых с кусочно-линейной шкалы stops. */
function sampleRamp(stops: string[], n: number): string[] {
  if (n <= 1) return [stops[stops.length - 1]]
  const rgb = stops.map(hexToRgb)
  return Array.from({ length: n }, (_, i) => {
    const t = (i / (n - 1)) * (rgb.length - 1)
    const k = Math.min(Math.floor(t), rgb.length - 2)
    const f = t - k
    return rgbToHex(rgb[k].map((c, j) => c + (rgb[k + 1][j] - c) * f))
  })
}

async function prepareStyle(layer: LandscapeMapLayer): Promise<PreparedStyle> {
  // Стиль из ГИС имеет приоритет; шкала по данным — только если SLD у слоя нет.
  const xml = layer.sldUrl ? await fetchLandscapeSld(layer.sldUrl) : null
  if (xml) {
    const cm = parseRasterColorMap(xml)
    if (cm) {
      return {
        tiles: landscapeTileTemplate(layer.datasetId, { colormap: rasterColorMapToTitiler(cm) }),
        legend: cm.entries.map((e) => ({ value: e.quantity, color: e.color, label: e.label })),
        fromSld: true,
      }
    }
  }
  const stats = await landscapeRasterStatistics(layer.datasetId)
  const count = stats.max - stats.min + 1
  if (stats.integer && count <= MAX_DISCRETE) {
    const colors = sampleRamp(layer.ramp, count)
    const legend = colors.map((color, i) => {
      const value = stats.min + i
      return { value, color, label: String(value) }
    })
    const colormap = Object.fromEntries(legend.map((l) => [String(l.value), l.color]))
    return { tiles: landscapeTileTemplate(layer.datasetId, { colormap }), legend, fromSld: false }
  }
  // Непрерывные значения: titiler colormap_name + rescale по min/max.
  return {
    tiles: landscapeTileTemplate(layer.datasetId, {
      colormapName: 'viridis',
      rescale: `${stats.min},${stats.max}`,
    }),
    legend: [],
    fromSld: false,
    gradient: {
      min: stats.min,
      max: stats.max,
      colors: ['#440154', '#414487', '#2a788e', '#22a884', '#7ad151', '#fde725'],
    },
  }
}

/**
 * Карта ландшафтного планирования: один растровый слой за раз (вариант V1–V3,
 * входной или промежуточный слой) поверх векторной подложки и границ районов.
 *
 * Карта создаётся один раз; при смене слоя заменяется только raster-источник.
 * Перед показом COG заливается в том titiler (ingest, идемпотентно — повторный
 * показ мгновенный). Раскраска и легенда — из ColorMap SLD (quantity/color/label):
 * у V1–V3 и промежуточных слоёв — sld_url из /landscape/results, у входов —
 * /datasets/{latest.id}/sld. Если SLD в ГИС нет (404), строится порядковая шкала
 * по фактическим целым значениям растра.
 */
export default function LandscapeRasterMap({ layer }: { layer: LandscapeMapLayer | null }) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapType | null>(null)
  const popupRef = useRef<PopupType | null>(null)
  const layerRef = useRef<LandscapeMapLayer | null>(null)
  const legendRef = useRef<LegendItem[]>([])
  const seqRef = useRef(0)
  const [ready, setReady] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)
  const [phase, setPhase] = useState<'idle' | 'loading' | 'shown' | 'error'>('idle')
  const [layerError, setLayerError] = useState<string | null>(null)
  const [style, setStyle] = useState<PreparedStyle | null>(null)
  const [retry, setRetry] = useState(0)

  // ── Инициализация карты (один раз) ────────────────────────────────────────
  useEffect(() => {
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
      popupRef.current = new maplibregl.Popup({ closeButton: true, closeOnClick: true })

      map = m
      m.on('load', async () => {
        if (cancelled) return
        removeBasemapBoundaries(m)
        const districts = await fetchDistrictBoundaries()
        if (cancelled) return
        m.addSource('lp-districts', { type: 'geojson', data: districts as never })
        m.addLayer({
          id: 'lp-districts-line',
          type: 'line',
          source: 'lp-districts',
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
            m.fitBounds(b, { padding: 24, duration: 0 })
          }
        } catch {
          /* охват — best effort */
        }

        m.on('click', async (e) => {
          const cur = layerRef.current
          if (!cur || !m.getLayer(LAYER)) return
          const val = await landscapeRasterPoint(cur.datasetId, e.lngLat.lng, e.lngLat.lat)
          if (val == null || cancelled) return
          const item = legendRef.current.find((l) => l.value === val)
          const text = item && item.label !== String(val) ? `${val} — ${item.label}` : String(val)
          popupRef.current
            ?.setLngLat(e.lngLat)
            .setHTML(`<div style="font-size:12px"><b>${escapeHtml(cur.title)}</b><br/>${escapeHtml(text)}</div>`)
            .addTo(m)
        })

        mapRef.current = m
        m.resize()
        setReady(true)
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
      setReady(false)
    }
  }, [])

  // ── Смена слоя: ingest → стиль → замена raster-источника ──────────────────
  useEffect(() => {
    layerRef.current = layer
    const map = mapRef.current
    if (!ready || !map) return
    const seq = ++seqRef.current
    const stale = () => seq !== seqRef.current || !mapRef.current

    popupRef.current?.remove()
    if (map.getLayer(LAYER)) map.removeLayer(LAYER)
    if (map.getSource(SRC)) map.removeSource(SRC)
    setStyle(null)
    legendRef.current = []
    setLayerError(null)
    if (!layer) {
      setPhase('idle')
      return
    }
    setPhase('loading')

    ;(async () => {
      try {
        await waitRasterIngest(layer.datasetId, stale)
        if (stale()) return
        const prepared = await prepareStyle(layer)
        if (stale()) return
        map.addSource(SRC, { type: 'raster', tiles: [prepared.tiles], tileSize: 256 })
        // Растр под границами районов.
        map.addLayer(
          { id: LAYER, type: 'raster', source: SRC, paint: { 'raster-opacity': 0.85, 'raster-resampling': 'nearest' } },
          map.getLayer('lp-districts-line') ? 'lp-districts-line' : undefined,
        )
        legendRef.current = prepared.legend
        setStyle(prepared)
        setPhase('shown')
      } catch (e) {
        if (stale()) return
        setLayerError(e instanceof Error && !('response' in e) ? e.message : apiErrorMessage(e, 'Не удалось показать слой'))
        setPhase('error')
      }
    })()
  }, [ready, layer?.datasetId, layer?.sldUrl, retry]) // eslint-disable-line react-hooks/exhaustive-deps

  const onRetry = useCallback(() => setRetry((r) => r + 1), [])

  if (mapError) {
    return (
      <div className="flex h-full min-h-[420px] items-center justify-center rounded-xl bg-gray-50 p-4 text-center text-sm text-gray-500">
        {mapError}
      </div>
    )
  }

  return (
    <div className="relative h-full min-h-[420px] w-full overflow-hidden rounded-xl border border-gray-200">
      {/* maplibre ставит контейнеру position: relative — абсолютная обёртка задаёт размер. */}
      <div className="absolute inset-0">
        <div ref={container} className="h-full w-full" />
      </div>

      {phase === 'loading' && (
        <div className="absolute left-1/2 top-3 -translate-x-1/2 rounded-full bg-white/95 px-4 py-1.5 text-xs text-gray-600 shadow">
          Подготовка слоя к отображению…
        </div>
      )}

      {phase === 'error' && layerError && (
        <div className="absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-xs text-rose-800 shadow">
          <span>{layerError}</span>
          <button onClick={onRetry} className="font-medium underline">
            Повторить
          </button>
        </div>
      )}

      {phase === 'shown' && style && layer && (
        <div className="absolute right-3 top-3 max-h-[calc(100%-24px)] max-w-[260px] overflow-y-auto rounded-lg bg-white/95 p-3 text-xs shadow">
          <div className="mb-2 font-semibold text-gray-900">{layer.title}</div>
          {style.legend.length > 0 && (
            <ul className="space-y-1">
              {style.legend.map((l) => (
                <li key={l.value} className="flex items-start gap-2">
                  <span className="mt-0.5 h-3 w-3 shrink-0 rounded-sm border border-black/10" style={{ background: l.color }} />
                  <span className="text-gray-700">
                    {style.fromSld ? `${l.value} — ${l.label}` : l.label}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {style.gradient && (
            <div>
              <div className="h-2.5 rounded" style={{ background: `linear-gradient(to right, ${style.gradient.colors.join(', ')})` }} />
              <div className="mt-1 flex justify-between text-gray-500">
                <span>{style.gradient.min}</span>
                <span>{style.gradient.max}</span>
              </div>
            </div>
          )}
          {!style.fromSld && (
            <p className="mt-2 text-[11px] leading-snug text-gray-400">
              Значения слоя; стиль в ГИС не задан — шкала построена по данным.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
