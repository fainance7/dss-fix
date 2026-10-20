'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { useAuthStore } from '@/store/authStore'
import 'maplibre-gl/dist/maplibre-gl.css'

// MapLibre types only — no SSR issues with type imports
import type { Map as MapType } from 'maplibre-gl'
import { fetchGeoJSON, fetchDataLayers, fetchShapefileList, fetchShapefileGeoJSON, fetchDistrictBoundaries, createAnnotation, fetchAnnotations, DATA_YEAR } from '@/lib/api/backend'
import type { DataLayer, MapAnnotation } from '@/lib/api/backend'
import { isWebGLAvailable, WEBGL_ERROR_MESSAGE } from '@/lib/webgl'
import { BASEMAP_STYLE, removeBasemapBoundaries } from '@/lib/map/basemap'

const SKO_CENTER: [number, number] = [69.09, 54.87] // Петропавловск
const SKO_ZOOM = 7.5

interface Layer {
  id: string
  label: string
  color: string
  defaultOn: boolean
  layerDbId?: string
}

const STATIC_LAYERS: Layer[] = [
  { id: 'districts', label: 'Районы', color: '#6366f1', defaultOn: true },
]

interface PopupInfo {
  district: string
  population: number
}

interface TooltipState {
  x: number
  y: number
  props: Record<string, unknown>
}

interface ToastState {
  id: number
  message: string
}

// Source/layer name constants to avoid collisions
const GEO_SOURCE_PREFIX = 'geo-src-'
const GEO_LAYER_PREFIX = 'geo-layer-'

const LAYER_COLORS = ['#22c55e', '#f59e0b', '#ef4444', '#06b6d4', '#8b5cf6', '#f43f5e']

// Per-district colors for SKO (data-driven MapLibre match expression), keyed by KATO code.
const DISTRICT_COLORS: Record<string, string> = {
  '591000000': '#6366f1', // Петропавловск г.а.
  '596000000': '#10b981', // Тайыншинский район
  '594600000': '#f59e0b', // Жамбылский район
  '595200000': '#8b5cf6', // Мамлютский район
  '595000000': '#06b6d4', // Кызылжарский район
  '593600000': '#f43f5e', // Район Магжана Жумабаева
  '596400000': '#84cc16', // Уалихановский район
  '593400000': '#fb923c', // Акжарский район
  '596200000': '#14b8a6', // Тимирязевский район
  '595600000': '#a855f7', // Район Шал акына
  '596600000': '#ec4899', // Район им. Габита Мусрепова
  '593200000': '#22c55e', // Айыртауский район
  '595800000': '#3b82f6', // Аккайынский район
  '594200000': '#ef4444', // Есильский район
}

// MapLibre match expression: ['match', ['get', 'kato'], kato1, color1, ..., fallback]
const DISTRICT_FILL_EXPRESSION = [
  'match', ['get', 'kato'],
  ...Object.entries(DISTRICT_COLORS).flatMap(([kato, color]) => [kato, color]),
  '#94a3b8',
]

export default function MapPage() {
  const mapContainer = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapType | null>(null)

  const [dynamicLayers, setDynamicLayers] = useState<Layer[]>([])
  const [shapefileLayers, setShapefileLayers] = useState<Layer[]>([])
  const [layerVisible, setLayerVisible] = useState<Record<string, boolean>>(
    Object.fromEntries(STATIC_LAYERS.map((l) => [l.id, l.defaultOn]))
  )
  const [year, setYear] = useState(DATA_YEAR)
  // Начало периода (ТЗ ФFE.1: временной слайдер или выбор диапазона дат).
  const [yearFrom, setYearFrom] = useState(DATA_YEAR)
  const [popup, setPopup] = useState<PopupInfo | null>(null)
  const [layersPanelOpen, setLayersPanelOpen] = useState(true)
  const [mapError, setMapError] = useState<string | null>(null)
  const [annotateOpen, setAnnotateOpen] = useState(false)
  const [annotationText, setAnnotationText] = useState('')
  const [annotationSaving, setAnnotationSaving] = useState(false)
  const [clickCoord, setClickCoord] = useState<{ lng: number; lat: number } | null>(null)
  const [toasts, setToasts] = useState<ToastState[]>([])
  const [mapReady, setMapReady] = useState(false)
  const [tooltip, setTooltip] = useState<TooltipState | null>(null)
  const activeLayerIdsRef = useRef<string[]>([])
  const [selectedDistrict, setSelectedDistrict] = useState<Record<string, unknown> | null>(null)
  const [statsPanelOpen, setStatsPanelOpen] = useState(true)

  const { hasRole } = useAuthStore()
  const canAnnotate = hasRole('exp_ver') || hasRole('admin')
  // Аннотации видят эксперты и администратор (ТЗ Прил. 2, ч. 1).
  const canViewAnnotations = canAnnotate || hasRole('exp_dir')
  const [annotations, setAnnotations] = useState<MapAnnotation[]>([])
  const [showAnnotations, setShowAnnotations] = useState(true)
  const annotationMarkersRef = useRef<{ remove: () => void }[]>([])

  const loadAnnotations = useCallback(() => {
    if (!canViewAnnotations) return
    fetchAnnotations({ onMap: true }).then(setAnnotations).catch(() => setAnnotations([]))
  }, [canViewAnnotations])

  useEffect(() => {
    loadAnnotations()
  }, [loadAnnotations])

  // Маркеры аннотаций: текст выводится через textContent — без разметки.
  useEffect(() => {
    let cancelled = false
    annotationMarkersRef.current.forEach((m) => m.remove())
    annotationMarkersRef.current = []
    const map = mapRef.current
    if (!mapReady || !map || !showAnnotations || annotations.length === 0) return
    import('maplibre-gl').then(({ default: maplibregl }) => {
      if (cancelled) return
      for (const a of annotations) {
        if (a.longitude == null || a.latitude == null) continue
        const box = document.createElement('div')
        box.className = 'text-xs max-w-[240px]'
        const head = document.createElement('div')
        head.className = 'mb-1 text-gray-500'
        head.textContent = `${a.author_name ?? 'эксперт'}, ${a.created_at ? new Date(a.created_at).toLocaleString('ru-RU') : ''}`
        const body = document.createElement('div')
        body.className = 'text-gray-900 whitespace-pre-wrap'
        body.textContent = a.comment
        box.append(head, body)
        const marker = new maplibregl.Marker({ color: '#dc2626', scale: 0.7 })
          .setLngLat([a.longitude, a.latitude])
          .setPopup(new maplibregl.Popup({ offset: 18 }).setDOMContent(box))
          .addTo(map)
        annotationMarkersRef.current.push(marker)
      }
    })
    return () => {
      cancelled = true
    }
  }, [annotations, mapReady, showAnnotations])

  const addToast = useCallback((message: string) => {
    const id = Date.now()
    setToasts((prev) => [...prev, { id, message }])
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3000)
  }, [])

  // Load shapefile list from backend
  useEffect(() => {
    fetchShapefileList().then((list) => {
      const built: Layer[] = list.map((s, i) => ({
        id: `shp-${s.name}`,
        label: s.display_name,
        color: LAYER_COLORS[(i + 3) % LAYER_COLORS.length],
        defaultOn: false,
        layerDbId: s.name,
      }))
      setShapefileLayers(built)
      setLayerVisible((prev) => ({
        ...prev,
        ...Object.fromEntries(built.map((l) => [l.id, false])),
      }))
    }).catch(() => {})
  }, [])

  // Load data layers from backend
  useEffect(() => {
    fetchDataLayers().then((layers: DataLayer[]) => {
      const built: Layer[] = layers.slice(0, 6).map((l, i) => ({
        id: `dl-${l.id}`,
        label: l.name,
        color: LAYER_COLORS[i % LAYER_COLORS.length],
        defaultOn: false,
        layerDbId: l.id,
      }))
      setDynamicLayers(built)
      setLayerVisible((prev) => ({
        ...prev,
        ...Object.fromEntries(built.map((l) => [l.id, l.defaultOn])),
      }))
    }).catch(() => {
      // Silent fail — no dynamic layers
    })
  }, [])

  // Initialize MapLibre on client only
  useEffect(() => {
    if (!mapContainer.current || mapRef.current) return

    let map: MapType

    async function initMap() {
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
        // Force recalculate canvas size after layout settles
        map.resize()
        setMapReady(true)
      })

      map.on('click', (e) => {
        // Запоминаем координату клика для привязки аннотации (G2)
        setClickCoord({ lng: e.lngLat.lng, lat: e.lngLat.lat })
        // Try to query features from any geo layer
        const allLayerIds = [
          ...STATIC_LAYERS.map((l) => GEO_LAYER_PREFIX + l.id),
        ]
        const features = map.queryRenderedFeatures(e.point, { layers: allLayerIds })
        if (features.length > 0) {
          const props = { ...features[0].properties } as Record<string, unknown>
          setSelectedDistrict(props)
          setStatsPanelOpen(true)
          setPopup({
            district: (props['name_ru'] as string) ?? (props['name'] as string) ?? (props['region_name'] as string) ?? 'Район',
            // Только явное поле численности: `value` слоя — произвольная метрика, не люди.
            population: Number(props['population'] ?? 0),
          })
        }
      })

      map.on('mousemove', (e) => {
        const ids = activeLayerIdsRef.current
        if (ids.length === 0) return
        const features = map.queryRenderedFeatures(e.point, { layers: ids })
        if (features.length > 0) {
          map.getCanvas().style.cursor = 'pointer'
          const props = { ...features[0].properties } as Record<string, unknown>
          // remove noisy/technical fields
          for (const k of ['OBJECTID', 'SHAPE_Leng', 'SHAPE_Area', 'ORIG_FID', 'Shape_Le_1', 'Shape_Area', 'Shape_Length']) {
            delete props[k]
          }
          setTooltip({ x: e.point.x, y: e.point.y, props })
        } else {
          map.getCanvas().style.cursor = ''
          setTooltip(null)
        }
      })

      map.on('mouseleave', () => {
        map.getCanvas().style.cursor = ''
        setTooltip(null)
      })
    }

    initMap().catch(console.error)

    // Resize map canvas whenever the container changes size
    const observer = new ResizeObserver(() => {
      mapRef.current?.resize()
    })
    if (mapContainer.current) {
      observer.observe(mapContainer.current)
    }

    return () => {
      observer.disconnect()
      if (mapRef.current) {
        mapRef.current.remove()
        mapRef.current = null
      }
    }
  }, [])

  // Load district boundaries once when map is ready (dedicated effect, no race with syncLayers)
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current
    const sourceId = GEO_SOURCE_PREFIX + 'districts'
    const layerId = GEO_LAYER_PREFIX + 'districts'

    if (map.getSource(sourceId)) return

    fetchDistrictBoundaries()
      .then((geojson) => {
        if (!mapRef.current || geojson.features.length === 0) return
        const m = mapRef.current
        if (m.getSource(sourceId)) return // already added by a concurrent call
        m.addSource(sourceId, {
          type: 'geojson',
          data: geojson as Parameters<import('maplibre-gl').GeoJSONSource['setData']>[0],
        })
        m.addLayer({
          id: layerId,
          type: 'fill',
          source: sourceId,
          paint: {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            'fill-color': DISTRICT_FILL_EXPRESSION as any,
            'fill-opacity': 0.45,
          },
        })
        m.addLayer({
          id: layerId + '-outline',
          type: 'line',
          source: sourceId,
          paint: {
            'line-color': '#ffffff',
            'line-width': 1.5,
            'line-opacity': 0.9,
          },
        })
        // Update hover/click ref to include the new layer
        activeLayerIdsRef.current = [
          ...activeLayerIdsRef.current.filter((id) => id !== layerId),
          layerId,
        ]
      })
      .catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady])

  // Sync GeoJSON layers to map when year or visibility changes
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current

    const allLayers = [...STATIC_LAYERS, ...dynamicLayers, ...shapefileLayers]

    const syncLayers = async () => {
      await Promise.all(allLayers.map(async (layer) => {
        const sourceId = GEO_SOURCE_PREFIX + layer.id
        const layerId = GEO_LAYER_PREFIX + layer.id
        const isOn = layerVisible[layer.id]

        // Districts source/layers are managed by the dedicated effect above;
        // here we only toggle their visibility.
        if (layer.id === 'districts') {
          if (map.getLayer(layerId)) {
            map.setLayoutProperty(layerId, 'visibility', isOn ? 'visible' : 'none')
          }
          if (map.getLayer(layerId + '-outline')) {
            map.setLayoutProperty(layerId + '-outline', 'visibility', isOn ? 'visible' : 'none')
          }
          return
        }

        if (isOn) {
          try {
            const geojson = layer.id.startsWith('shp-')
              ? await fetchShapefileGeoJSON(layer.layerDbId!)
              : await fetchGeoJSON({
                  layer_id: layer.layerDbId,
                  date_from: `${Math.min(yearFrom, year)}-01-01`,
                  // Отчётный год включает и более поздние срезы (приведение к DATA_YEAR).
                  date_to: year === DATA_YEAR ? '2100-12-31' : `${year}-12-31`,
                })

            if (geojson.features.length === 0) return

            if (map.getSource(sourceId)) {
              const src = map.getSource(sourceId)
              if (src && src.type === 'geojson') {
                (src as import('maplibre-gl').GeoJSONSource).setData(geojson as Parameters<import('maplibre-gl').GeoJSONSource['setData']>[0])
              }
            } else {
              map.addSource(sourceId, {
                type: 'geojson',
                data: geojson as Parameters<import('maplibre-gl').GeoJSONSource['setData']>[0],
              })
            }

            if (!map.getLayer(layerId)) {
              const geomType = geojson.features[0]?.geometry?.type ?? ''
              const isLine = geomType.includes('LineString')
              const isPoint = geomType.includes('Point')

              if (isLine) {
                map.addLayer({
                  id: layerId,
                  type: 'line',
                  source: sourceId,
                  paint: {
                    'line-color': layer.color,
                    'line-width': 2,
                    'line-opacity': 0.85,
                  },
                })
              } else if (isPoint) {
                map.addLayer({
                  id: layerId,
                  type: 'circle',
                  source: sourceId,
                  paint: {
                    'circle-color': layer.color,
                    'circle-radius': 5,
                    'circle-opacity': 0.85,
                    'circle-stroke-width': 1,
                    'circle-stroke-color': '#fff',
                  },
                })
              } else {
                map.addLayer({
                  id: layerId,
                  type: 'fill',
                  source: sourceId,
                  paint: {
                    'fill-color': layer.color,
                    'fill-opacity': 0.35,
                    'fill-outline-color': layer.color,
                  },
                })
              }
            } else {
              map.setLayoutProperty(layerId, 'visibility', 'visible')
            }
          } catch {
            // Silent fail for this layer
          }
        } else {
          if (map.getLayer(layerId)) {
            map.setLayoutProperty(layerId, 'visibility', 'none')
          }
        }
      }))

      // Update ref after all layers are added to the map
      activeLayerIdsRef.current = allLayers
        .filter((l) => layerVisible[l.id])
        .map((l) => GEO_LAYER_PREFIX + l.id)
        .filter((id) => map.getLayer(id) !== undefined)
    }

    syncLayers()
  }, [mapReady, layerVisible, year, yearFrom, dynamicLayers, shapefileLayers])

  function handleZoomIn() {
    mapRef.current?.zoomIn()
  }
  function handleZoomOut() {
    mapRef.current?.zoomOut()
  }
  function handleReset() {
    mapRef.current?.flyTo({ center: SKO_CENTER, zoom: SKO_ZOOM })
  }

  async function handleAnnotationSave() {
    if (!annotationText.trim() || annotationSaving) return
    // Точка привязки: последний клик по карте, иначе — центр текущего вида
    const coord = clickCoord ?? (mapRef.current
      ? { lng: mapRef.current.getCenter().lng, lat: mapRef.current.getCenter().lat }
      : null)
    if (!coord) {
      addToast('Не удалось определить точку — кликните по карте')
      return
    }
    setAnnotationSaving(true)
    try {
      await createAnnotation({
        comment: annotationText.trim(),
        longitude: coord.lng,
        latitude: coord.lat,
      })
      addToast('Аннотация сохранена')
      setAnnotationText('')
      setAnnotateOpen(false)
      loadAnnotations()
    } catch {
      addToast('Ошибка сохранения аннотации')
    } finally {
      setAnnotationSaving(false)
    }
  }

  const allLayers = [...STATIC_LAYERS, ...dynamicLayers, ...shapefileLayers]

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Map wrapper fills all available height */}
      <div style={{ position: 'relative', flex: '1 1 0', minHeight: 0, overflow: 'hidden' }}>
        {/* Map container — explicit 100% so MapLibre reads correct clientHeight */}
        <div ref={mapContainer} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
          {/* Placeholder shown while map loads, or WebGL error message */}
          <div
            className={`flex h-full items-center justify-center px-6 text-center text-sm ${
              mapError ? 'bg-red-50 text-red-700' : 'bg-gray-100 text-gray-400'
            }`}
          >
            <span className="max-w-md">{mapError ?? 'Загрузка карты...'}</span>
          </div>
        </div>

        {/* Layers panel */}
        <div
          className={`absolute left-3 top-3 z-10 w-64 rounded-xl bg-white shadow-lg ring-1 ring-gray-200 transition-all duration-200 ${
            layersPanelOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
          }`}
        >
          <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
            <h3 className="text-sm font-semibold text-gray-900">Слои</h3>
            <button
              onClick={() => setLayersPanelOpen(false)}
              className="text-gray-400 hover:text-gray-700 text-base leading-none"
              aria-label="Закрыть панель слоёв"
            >
              ×
            </button>
          </div>
          <div className="p-3 space-y-2 max-h-[32rem] overflow-y-auto">
            {[...STATIC_LAYERS, ...dynamicLayers].map((layer) => (
              <label
                key={layer.id}
                className="flex cursor-pointer items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-gray-50"
              >
                <input
                  type="checkbox"
                  checked={layerVisible[layer.id] ?? false}
                  onChange={(e) =>
                    setLayerVisible((prev) => ({ ...prev, [layer.id]: e.target.checked }))
                  }
                  className="h-4 w-4 rounded accent-primary-600"
                />
                {layer.id === 'districts' ? (
                  <span
                    className="h-3 w-3 rounded-sm flex-shrink-0 overflow-hidden"
                    style={{
                      background: `linear-gradient(135deg, ${Object.values(DISTRICT_COLORS).slice(0, 4).join(', ')})`,
                    }}
                  />
                ) : (
                  <span
                    className="h-3 w-3 rounded-sm flex-shrink-0"
                    style={{ backgroundColor: layer.color }}
                  />
                )}
                <span className="text-sm text-gray-700">{layer.label}</span>
              </label>
            ))}
            {shapefileLayers.length > 0 && (
              <>
                <div className="pt-1 pb-0.5 text-xs font-semibold text-gray-400 uppercase tracking-wide px-2">
                  Шейп-файлы
                </div>
                {shapefileLayers.map((layer) => (
                  <label
                    key={layer.id}
                    className="flex cursor-pointer items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-gray-50"
                  >
                    <input
                      type="checkbox"
                      checked={layerVisible[layer.id] ?? false}
                      onChange={(e) =>
                        setLayerVisible((prev) => ({ ...prev, [layer.id]: e.target.checked }))
                      }
                      className="h-4 w-4 rounded accent-primary-600"
                    />
                    <span
                      className="h-3 w-3 rounded-sm flex-shrink-0"
                      style={{ backgroundColor: layer.color }}
                    />
                    <span className="text-sm text-gray-700">{layer.label}</span>
                  </label>
                ))}
              </>
            )}
          </div>
          {/* Year slider */}
          <div className="border-t border-gray-100 px-4 pb-4 pt-3">
            <div className="mb-2 flex justify-between text-xs text-gray-500">
              <span>Период данных</span>
              <span className="font-semibold text-primary-600">
                {yearFrom < year ? `${yearFrom}–${year}` : year}
              </span>
            </div>
            <label className="flex items-center gap-2 text-xs text-gray-500">
              <span className="w-4">с</span>
              <input
                type="range"
                min={2015}
                max={DATA_YEAR}
                value={yearFrom}
                onChange={(e) => {
                  const v = Number(e.target.value)
                  setYearFrom(v)
                  if (v > year) setYear(v)
                }}
                className="w-full accent-primary-600"
              />
            </label>
            <label className="flex items-center gap-2 text-xs text-gray-500">
              <span className="w-4">по</span>
              <input
                type="range"
                min={2015}
                max={DATA_YEAR}
                value={year}
                onChange={(e) => {
                  const v = Number(e.target.value)
                  setYear(v)
                  if (v < yearFrom) setYearFrom(v)
                }}
                className="w-full accent-primary-600"
              />
            </label>
            <div className="mt-1 flex justify-between text-xs text-gray-400">
              <span>2015</span>
              <span>{DATA_YEAR}</span>
            </div>
          </div>
        </div>

        {/* Toggle layers panel button */}
        {!layersPanelOpen && (
          <button
            onClick={() => setLayersPanelOpen(true)}
            className="absolute left-3 top-3 z-10 rounded-lg bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-lg ring-1 ring-gray-200 hover:bg-gray-50"
          >
            &#9776; Слои
          </button>
        )}

        {/* Map controls (top-right) */}
        <div className="absolute right-3 top-3 z-10 flex flex-col gap-1">
          {[
            { label: '+', title: 'Увеличить', onClick: handleZoomIn },
            { label: '−', title: 'Уменьшить', onClick: handleZoomOut },
            { label: '⌖', title: 'Сброс', onClick: handleReset },
          ].map((ctrl) => (
            <button
              key={ctrl.label}
              title={ctrl.title}
              onClick={ctrl.onClick}
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-white text-lg text-gray-700 shadow-lg ring-1 ring-gray-200 hover:bg-gray-50"
            >
              {ctrl.label}
            </button>
          ))}
          {canViewAnnotations && (
            <button
              title={showAnnotations ? `Скрыть аннотации (${annotations.length})` : `Показать аннотации (${annotations.length})`}
              onClick={() => setShowAnnotations((v) => !v)}
              className={`flex h-9 w-9 items-center justify-center rounded-lg text-lg shadow-lg ring-1 ring-gray-200 ${showAnnotations ? 'bg-red-50 text-red-600' : 'bg-white text-gray-400'}`}
            >
              &#9873;
            </button>
          )}
          {canAnnotate && (
            <button
              title="Добавить аннотацию"
              onClick={() => setAnnotateOpen(true)}
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-600 text-white shadow-lg hover:bg-primary-700"
            >
              &#9998;
            </button>
          )}
        </div>

        {/* Hover tooltip */}
        {tooltip && (
          <div
            className="pointer-events-none absolute z-30 max-w-[260px] rounded-lg bg-gray-900/90 px-3 py-2 text-xs text-white shadow-xl backdrop-blur-sm"
            style={{ left: tooltip.x + 14, top: tooltip.y - 10 }}
          >
            {Object.entries(tooltip.props)
              .filter(([, v]) => v !== null && v !== undefined && v !== 'null' && String(v).trim() !== '')
              .map(([k, v]) => (
                <div key={k} className="flex gap-2 justify-between">
                  <span className="text-gray-400 shrink-0">{k}</span>
                  <span className="font-medium text-right truncate max-w-[140px]">{String(v)}</span>
                </div>
              ))}
          </div>
        )}

        {/* Feature popup */}
        {popup && (
          <div className="absolute bottom-10 left-1/2 z-20 -translate-x-1/2 rounded-xl bg-white shadow-xl ring-1 ring-gray-200 min-w-[220px]">
            <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
              <h4 className="font-semibold text-gray-900">{popup.district}</h4>
              <button
                onClick={() => setPopup(null)}
                className="text-gray-400 hover:text-gray-700 leading-none"
              >
                ×
              </button>
            </div>
            <div className="p-4 space-y-2 text-sm">
              {popup.population > 0 && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Население</span>
                  <span className="font-medium">{popup.population.toLocaleString('ru-RU')} чел.</span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Stats Dashboard Panel (right side, below map controls) */}
        <div
          className={`absolute right-3 z-10 w-72 rounded-xl bg-white shadow-lg ring-1 ring-gray-200 transition-all duration-200 ${
            statsPanelOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
          }`}
          style={{ top: '12rem' }}
        >
          <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
            <h3 className="text-sm font-semibold text-gray-900">
              {selectedDistrict
                ? ((selectedDistrict['name'] as string) ?? (selectedDistrict['region_name'] as string) ?? 'Район')
                : 'Дашборд — СКО'}
            </h3>
            <div className="flex items-center gap-2">
              {selectedDistrict && (
                <button
                  onClick={() => { setSelectedDistrict(null); setPopup(null) }}
                  className="text-xs text-primary-600 hover:text-primary-800 font-medium"
                >
                  ← область
                </button>
              )}
              <button
                onClick={() => setStatsPanelOpen(false)}
                className="text-gray-400 hover:text-gray-700 text-base leading-none"
                aria-label="Закрыть"
              >
                ×
              </button>
            </div>
          </div>

          <div className="p-3 space-y-1 max-h-[calc(100vh-22rem)] overflow-y-auto">
            {selectedDistrict ? (
              <div className="space-y-0.5 text-sm">
                {Object.entries(selectedDistrict)
                  .filter(([k, v]) =>
                    !['OBJECTID', 'SHAPE_Leng', 'SHAPE_Area', 'ORIG_FID', 'Shape_Le_1', 'Shape_Area'].includes(k) &&
                    v !== null && v !== undefined && v !== 'null' && String(v).trim() !== ''
                  )
                  .map(([k, v]) => (
                    <div key={k} className="flex justify-between items-center py-1.5 border-b border-gray-50">
                      <span className="text-gray-500">{k.replace(/_/g, ' ')}</span>
                      <span className="font-medium text-gray-800 text-right max-w-[160px] truncate">{String(v)}</span>
                    </div>
                  ))}
              </div>
            ) : (
              <div className="space-y-2 text-sm">
                <div className="rounded-lg bg-indigo-50 p-3">
                  <div className="text-xs text-indigo-600 font-medium mb-1">Регион</div>
                  <div className="font-semibold text-gray-900">Северо-Казахстанская область</div>
                  <div className="text-xs text-gray-500 mt-1">13 районов + г. Петропавловск</div>
                </div>
                <div className="flex justify-between items-center py-1.5 border-b border-gray-50">
                  <span className="text-gray-500">Активных слоёв</span>
                  <span className="font-semibold text-gray-800">{Object.values(layerVisible).filter(Boolean).length}</span>
                </div>
                <div className="flex justify-between items-center py-1.5 border-b border-gray-50">
                  <span className="text-gray-500">Период данных</span>
                  <span className="font-semibold text-primary-600">{yearFrom < year ? `${yearFrom}–${year}` : year}</span>
                </div>
                <div className="pt-1 text-xs text-gray-400 text-center">
                  Нажмите на район для детальной информации
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Toggle stats panel button */}
        {!statsPanelOpen && (
          <button
            onClick={() => setStatsPanelOpen(true)}
            className="absolute right-3 z-10 rounded-lg bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-lg ring-1 ring-gray-200 hover:bg-gray-50"
            style={{ top: '12rem' }}
          >
            📊 Дашборд
          </button>
        )}

        {/* Toast notifications */}
        <div
          className="absolute bottom-4 right-4 z-30 flex flex-col gap-2"
          aria-live="polite"
          aria-atomic="true"
        >
          {toasts.map((t) => (
            <div
              key={t.id}
              className="rounded-lg bg-gray-900 px-4 py-2.5 text-sm text-white shadow-lg"
            >
              {t.message}
            </div>
          ))}
        </div>
      </div>

      {/* Annotation modal */}
      {annotateOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="annotate-title"
        >
          <div className="w-full max-w-md rounded-xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <h3 id="annotate-title" className="font-semibold text-gray-900">
                Добавить аннотацию
              </h3>
              <button
                onClick={() => setAnnotateOpen(false)}
                className="text-gray-400 hover:text-gray-700 text-xl leading-none"
                aria-label="Закрыть"
              >
                ×
              </button>
            </div>
            <div className="p-6">
              <label className="mb-1 block text-sm font-medium text-gray-700">
                Комментарий
              </label>
              <textarea
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                rows={4}
                value={annotationText}
                onChange={(e) => setAnnotationText(e.target.value)}
                placeholder="Введите аннотацию к выделенному объекту..."
                autoFocus
              />
              <p className="mt-2 text-xs text-gray-500">
                {clickCoord
                  ? `Точка привязки: ${clickCoord.lat.toFixed(4)}, ${clickCoord.lng.toFixed(4)} (последний клик по карте)`
                  : 'Кликните по карте, чтобы задать точку привязки. Иначе аннотация встанет в центр текущего вида.'}
              </p>
            </div>
            <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
              <button
                onClick={() => setAnnotateOpen(false)}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                Отмена
              </button>
              <button
                onClick={handleAnnotationSave}
                disabled={!annotationText.trim() || annotationSaving}
                className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {annotationSaving ? 'Сохранение…' : 'Сохранить'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
