'use client'

import { useEffect, useRef, useState } from 'react'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Map as MapType, GeoJSONSource } from 'maplibre-gl'
import { BASEMAP_STYLE, removeBasemapBoundaries } from '@/lib/map/basemap'
import { isWebGLAvailable, WEBGL_ERROR_MESSAGE } from '@/lib/webgl'
import {
  gisRasterInfo,
  gisRasterStatistics,
  gisRasterPoint,
  gisRasterTileTemplate,
  type GeoJsonFeatureCollection,
} from '@/lib/api/backend'
import { DEFAULT_RASTER_COLORMAP } from '@/lib/gis/rasterColormaps'
import type { SldStyle } from '@/lib/gis/sld'
import { withApiCredentials } from '@/lib/session'

const SKO_CENTER: [number, number] = [69.09, 54.5]
const SKO_ZOOM = 6.2

export interface RasterLayerOpts {
  id: string
  bbox?: number[] | null
  colormap?: string
  unit?: string | null
}

function bboxToFeatureCollection(bb: number[]): GeoJsonFeatureCollection {
  const [minX, minY, maxX, maxY] = bb
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: {},
        geometry: {
          type: 'Polygon',
          coordinates: [[[minX, minY], [maxX, minY], [maxX, maxY], [minX, maxY], [minX, minY]]],
        },
      },
    ],
  }
}

/**
 * Превью геометрии датасета GIS. Рисует FeatureCollection (vector/vector_with_kato)
 * поверх векторной подложки, либо прямоугольник bbox (raster). Подложка и удаление
 * гос-/админграниц переиспользуются из lib/map/basemap.
 *
 * Choropleth: если у фич есть properties.__fill (обогащены в lib/gis/choropleth),
 * заливка идёт по значению, показываются подписи __val и всплывающая подсказка
 * (properties.name + __val). Иначе — плоская заливка fillColor.
 *
 * SLD: если передан разобранный стиль (lib/gis/sld), раскраска берётся из него —
 * paint-свойства слоёв заменяются выражениями по правилам SLD, choropleth не
 * применяется. Приоритет: sld > __fill > плоская заливка.
 *
 * Карта создаётся один раз; смена geojson (например при выборе года в choropleth)
 * обновляет источник через setData без пересоздания инстанса maplibre — заливка и
 * подписи, привязанные к __fill/__val, перечитываются автоматически.
 */
export default function DatasetGeojsonMap({
  geojson,
  bbox,
  fillColor = '#2563eb',
  raster,
  sld,
}: {
  geojson?: GeoJsonFeatureCollection | null
  bbox?: number[] | null
  fillColor?: string
  raster?: RasterLayerOpts | null
  sld?: SldStyle | null
}) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapType | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const maplibreRef = useRef<any>(null)
  const vectorInitRef = useRef(false) // добавлены ли уже слои/попапы vector-источника
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ── Инициализация карты (один раз на mount / смену растрового id) ──────────
  useEffect(() => {
    let map: MapType | undefined
    let cancelled = false
    vectorInitRef.current = false

    async function init() {
      if (!isWebGLAvailable()) {
        setError(WEBGL_ERROR_MESSAGE)
        return
      }
      const maplibregl = (await import('maplibre-gl')).default
      if (cancelled || !container.current) return
      map = new maplibregl.Map({
        container: container.current,
        style: BASEMAP_STYLE,
        center: SKO_CENTER,
        zoom: SKO_ZOOM,
        attributionControl: false,
        // Тайлы грузит сам maplibre (вне axios): запросам к API нужны cookie сеанса.
        transformRequest: withApiCredentials,
      })
      map.on('load', () => {
        if (!map || cancelled) return
        mapRef.current = map
        maplibreRef.current = maplibregl
        removeBasemapBoundaries(map)
        map.resize()
        if (raster) drawRaster(map, maplibregl, cancelledRef)
        setReady(true)
      })
    }

    // Позволяет асинхронному drawRaster узнать об анмаунте.
    const cancelledRef = { get value() { return cancelled } }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async function drawRaster(m: MapType, maplibregl: any, cref: { value: boolean }) {
      const r = raster!
      // Параллельно: статистика (min/max для rescale) и info (nodata).
      const [stats, info] = await Promise.all([gisRasterStatistics(r.id), gisRasterInfo(r.id)])
      if (cref.value || !mapRef.current) return

      // COG не залит / titiler недоступен → показать контур bbox как раньше.
      if (!stats) {
        if (r.bbox && r.bbox.length === 4) {
          m.addSource('bbox', { type: 'geojson', data: bboxToFeatureCollection(r.bbox) })
          m.addLayer({ id: 'bbox-fill', type: 'fill', source: 'bbox', paint: { 'fill-color': '#d97706', 'fill-opacity': 0.15 } })
          m.addLayer({ id: 'bbox-line', type: 'line', source: 'bbox', paint: { 'line-color': '#d97706', 'line-width': 1.5 } })
          const [minX, minY, maxX, maxY] = r.bbox
          m.fitBounds([[minX, minY], [maxX, maxY]], { padding: 24, duration: 0 })
        }
        return
      }

      const nodata = (info?.nodata_value ?? info?.nodata) as number | undefined
      const template = gisRasterTileTemplate(r.id, {
        colormap: r.colormap ?? DEFAULT_RASTER_COLORMAP,
        rescale: `${stats.min},${stats.max}`,
        nodata,
      })
      m.addSource('raster-ds', {
        type: 'raster',
        tiles: [template],
        tileSize: 256,
        ...(r.bbox && r.bbox.length === 4 ? { bounds: r.bbox as [number, number, number, number] } : {}),
      })
      m.addLayer({ id: 'raster-ds-layer', type: 'raster', source: 'raster-ds', paint: { 'raster-opacity': 0.85 } })

      if (r.bbox && r.bbox.length === 4) {
        const [minX, minY, maxX, maxY] = r.bbox
        m.fitBounds([[minX, minY], [maxX, maxY]], { padding: 24, duration: 0 })
      }

      // Значение по клику.
      const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: true })
      m.on('click', async (e: import('maplibre-gl').MapMouseEvent) => {
        const val = await gisRasterPoint(r.id, e.lngLat.lng, e.lngLat.lat)
        if (val == null || !mapRef.current) return
        const unit = r.unit ? ` ${r.unit}` : ''
        popup
          .setLngLat(e.lngLat)
          .setHTML(`<div style="font-size:12px">${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 3 }).format(val)}${unit}</div>`)
          .addTo(m)
      })
    }

    init().catch((e) => setError(String(e)))

    const observer = new ResizeObserver(() => mapRef.current?.resize())
    if (container.current) observer.observe(container.current)

    return () => {
      cancelled = true
      observer.disconnect()
      if (mapRef.current) {
        mapRef.current.remove()
        mapRef.current = null
      }
      maplibreRef.current = null
      setReady(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [raster?.id, raster?.colormap])

  // ── Обновление векторных данных (setData вместо ремоунта) ──────────────────
  useEffect(() => {
    if (raster) return
    const m = mapRef.current
    const maplibregl = maplibreRef.current
    if (!ready || !m || !maplibregl) return

    // Данные из geojson (vector) или прямоугольника bbox.
    let fc: GeoJsonFeatureCollection | null = geojson ?? null
    if (!fc && bbox && bbox.length === 4) fc = bboxToFeatureCollection(bbox)
    if (!fc || fc.features.length === 0) return

    // Смена года в choropleth: источник уже есть → только обновляем данные,
    // не трогая слои и не пересчитывая охват (сохраняем текущий вид).
    const existing = m.getSource('ds') as GeoJSONSource | undefined
    if (existing && vectorInitRef.current) {
      existing.setData(fc as Parameters<GeoJSONSource['setData']>[0])
      return
    }

    // Первый рендер: добавляем источник, слои, подписи и подсказки.
    const isChoro =
      !sld &&
      fc.features.some(
        (f) => f.properties && typeof f.properties === 'object' && '__fill' in f.properties,
      )

    m.addSource('ds', {
      type: 'geojson',
      data: fc as Parameters<GeoJSONSource['setData']>[0],
    })
    m.addLayer({
      id: 'ds-fill',
      type: 'fill',
      source: 'ds',
      filter: ['match', ['geometry-type'], ['Polygon', 'MultiPolygon'], true, false],
      paint: sld
        ? {
            'fill-color': sld.fill?.color ?? 'transparent',
            'fill-opacity': sld.fill?.opacity ?? (sld.fill ? 1 : 0),
          }
        : {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            'fill-color': (isChoro ? ['get', '__fill'] : fillColor) as any,
            'fill-opacity': isChoro ? 0.85 : 0.35,
          },
    })
    m.addLayer({
      id: 'ds-line',
      type: 'line',
      source: 'ds',
      paint: sld
        ? {
            'line-color': sld.line?.color ?? sld.outline?.color ?? 'transparent',
            'line-width': sld.line?.width ?? sld.outline?.width ?? 0,
          }
        : {
            'line-color': isChoro ? '#334155' : fillColor,
            'line-width': isChoro ? 0.8 : 1.5,
            'line-opacity': isChoro ? 0.6 : 1,
          },
    })
    m.addLayer({
      id: 'ds-point',
      type: 'circle',
      source: 'ds',
      filter: ['match', ['geometry-type'], ['Point', 'MultiPoint'], true, false],
      paint: sld?.circle
        ? {
            'circle-color': sld.circle.color,
            'circle-radius': sld.circle.radius,
            'circle-stroke-color': sld.circle.strokeColor,
            'circle-stroke-width': sld.circle.strokeWidth,
          }
        : { 'circle-color': fillColor, 'circle-radius': 4, 'circle-opacity': 0.8 },
    })

    // Подсказка по атрибутам, участвующим в правилах SLD, — чтобы проверить,
    // что цвет соответствует значению атрибута.
    if (sld && sld.propertyNames.length > 0) {
      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false })
      const showFor = (layerId: string) => {
        m.on('mousemove', layerId, (e: import('maplibre-gl').MapLayerMouseEvent) => {
          const f = e.features?.[0]
          if (!f) return
          m.getCanvas().style.cursor = 'pointer'
          const p = (f.properties ?? {}) as Record<string, unknown>
          const rows = sld.propertyNames
            .map((n) => `<div><b>${n}:</b> ${p[n] == null ? '—' : String(p[n])}</div>`)
            .join('')
          popup.setLngLat(e.lngLat).setHTML(`<div style="font-size:12px">${rows}</div>`).addTo(m)
        })
        m.on('mouseleave', layerId, () => {
          m.getCanvas().style.cursor = ''
          popup.remove()
        })
      }
      showFor('ds-fill')
      showFor('ds-point')
    }

    if (isChoro) {
      m.addLayer({
        id: 'ds-label',
        type: 'symbol',
        source: 'ds',
        filter: ['match', ['geometry-type'], ['Polygon', 'MultiPolygon'], true, false],
        layout: {
          'text-field': ['coalesce', ['get', '__val'], ''],
          'text-size': 11,
          'text-anchor': 'center',
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': '#0f172a',
          'text-halo-color': '#ffffff',
          'text-halo-width': 1.4,
        },
      })

      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false })
      m.on('mousemove', 'ds-fill', (e: import('maplibre-gl').MapLayerMouseEvent) => {
        const f = e.features?.[0]
        if (!f) return
        m.getCanvas().style.cursor = 'pointer'
        const p = (f.properties ?? {}) as Record<string, unknown>
        const name = (p.name as string) ?? (p.name_ru as string) ?? 'Район'
        const val = p.__val != null && p.__val !== '' ? String(p.__val) : '—'
        popup.setLngLat(e.lngLat).setHTML(`<div style="font-size:12px"><b>${name}</b><br/>${val}</div>`).addTo(m)
      })
      m.on('mouseleave', 'ds-fill', () => {
        m.getCanvas().style.cursor = ''
        popup.remove()
      })
    }

    // Автоподгон под охват данных — только при первом рендере.
    try {
      const b = new maplibregl.LngLatBounds()
      const extend = (coords: unknown) => {
        if (typeof (coords as number[])[0] === 'number' && (coords as number[]).length >= 2) {
          b.extend(coords as [number, number])
        } else if (Array.isArray(coords)) {
          for (const c of coords) extend(c)
        }
      }
      for (const f of fc.features) {
        if (f.geometry?.coordinates) extend(f.geometry.coordinates)
      }
      if (!b.isEmpty()) m.fitBounds(b, { padding: 24, maxZoom: 11, duration: 0 })
    } catch {
      /* fit — best effort */
    }

    vectorInitRef.current = true
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, geojson, bbox, fillColor, raster, sld])

  if (error) {
    return (
      <div className="flex h-full min-h-[240px] items-center justify-center rounded-lg bg-gray-50 p-4 text-center text-sm text-gray-500">
        {error}
      </div>
    )
  }

  return <div ref={container} className="h-full min-h-[320px] w-full rounded-lg" />
}
