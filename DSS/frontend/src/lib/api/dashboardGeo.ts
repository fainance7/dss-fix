// Карты из каталога геоданных на дашбордах направлений (/api/v1/dashboard-geo).
// Слои адресуются ключом из утверждённого перечня на сервере, а не id набора.
// Ответы кэшируются на уровне модуля: при переключении вкладок и повторном
// открытии дашборда слой не скачивается заново.

import api from '@/lib/api'

export interface GeoLayerInfo {
  key: string
  title: string
  kind: 'vector' | 'raster'
  description: string
  popup: string[]
  category_prop: string | null
  colormap_name: string
  ramp: string[]
  /** Подписи классов 1..N целочисленного растра, если их смысл известен. */
  class_labels: string[]
  unit: string
  dataset_id?: string
  dataset_name?: string
  start_date?: string | null
  end_date?: string | null
  has_sld?: boolean
  size_bytes?: number | null
  /** Слой недоступен (набор не найден, каталог не ответил). */
  error?: string
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type GeoFeatureCollection = { type: 'FeatureCollection'; features: any[] }

const infoCache = new Map<string, Promise<GeoLayerInfo[]>>()
const geojsonCache = new Map<string, Promise<GeoFeatureCollection>>()
const sldCache = new Map<string, Promise<string | null>>()

function once<T>(cache: Map<string, Promise<T>>, key: string, load: () => Promise<T>): Promise<T> {
  let p = cache.get(key)
  if (!p) {
    p = load().catch((e) => {
      cache.delete(key) // ошибку не кэшируем — повторная попытка пойдёт заново
      throw e
    })
    cache.set(key, p)
  }
  return p
}

export function fetchGeoLayers(keys: string[]): Promise<GeoLayerInfo[]> {
  const k = keys.join(',')
  return once(infoCache, k, async () => {
    const { data } = await api.get<GeoLayerInfo[]>('/dashboard-geo/layers', { params: { keys: k } })
    return data
  })
}

export function fetchGeoLayerGeojson(key: string): Promise<GeoFeatureCollection> {
  return once(geojsonCache, key, async () => {
    const { data } = await api.get<GeoFeatureCollection>(`/dashboard-geo/layers/${key}/geojson`)
    return data
  })
}

export function fetchGeoLayerSld(key: string): Promise<string | null> {
  return once(sldCache, key, async () => {
    try {
      const { data } = await api.get<string>(`/dashboard-geo/layers/${key}/sld`, { responseType: 'text' })
      return data
    } catch (e) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((e as any)?.response?.status === 404) return null
      throw e
    }
  })
}

interface IngestStatus {
  status: 'running' | 'done' | 'error' | 'absent'
  downloaded?: number
  total?: number | null
  error?: string | null
}

/** Залить COG растра в том titiler и дождаться готовности; onProgress — доля 0..1. */
export async function waitGeoRaster(
  key: string,
  isStale: () => boolean,
  onProgress?: (share: number | null) => void,
): Promise<void> {
  let { data: st } = await api.post<IngestStatus>(`/dashboard-geo/layers/${key}/ingest`)
  while (st.status === 'running') {
    if (st.total && st.downloaded != null) onProgress?.(st.downloaded / st.total)
    await new Promise((r) => setTimeout(r, 1500))
    if (isStale()) return
    st = (await api.get<IngestStatus>(`/dashboard-geo/layers/${key}/ingest/status`)).data
  }
  if (st.status !== 'done') throw new Error(st.error || 'Не удалось подготовить растр к отображению')
}

export interface GeoRasterStats {
  min: number
  max: number
  /** 2-й и 98-й процентили — границы шкалы для непрерывных значений. */
  p2: number
  p98: number
  integer: boolean
}

const statsCache = new Map<string, Promise<GeoRasterStats>>()

export function fetchGeoRasterStats(key: string): Promise<GeoRasterStats> {
  return once(statsCache, key, async () => {
    const { data } = await api.get<
      Record<string, { min: number; max: number; percentile_2?: number; percentile_98?: number }>
    >(`/dashboard-geo/layers/${key}/statistics`)
    const band = Object.values(data)[0]
    const p2 = band.percentile_2 ?? band.min
    const p98 = band.percentile_98 ?? band.max
    return {
      min: band.min,
      max: band.max,
      p2: p98 > p2 ? p2 : band.min,
      p98: p98 > p2 ? p98 : band.max,
      integer: Number.isInteger(band.min) && Number.isInteger(band.max),
    }
  })
}

export async function fetchGeoRasterPoint(key: string, lon: number, lat: number): Promise<number | null> {
  try {
    const { data } = await api.get<{ values?: number[] }>(`/dashboard-geo/layers/${key}/point`, {
      params: { lon, lat },
    })
    const v = data.values?.[0]
    return v == null || Number.isNaN(v) ? null : v
  } catch {
    return null
  }
}

function apiBase(): string {
  return (process.env.NEXT_PUBLIC_API_URL ?? '/api/v1').replace(/\/$/, '')
}

export function geoTileTemplate(
  key: string,
  opts: { colormap?: Record<string, string>; colormapName?: string; rescale?: string },
): string {
  const p = new URLSearchParams()
  if (opts.colormap) p.set('colormap', JSON.stringify(opts.colormap))
  else p.set('colormap_name', opts.colormapName ?? 'viridis')
  if (opts.rescale) p.set('rescale', opts.rescale)
  return `${apiBase()}/dashboard-geo/layers/${key}/tiles/{z}/{x}/{y}.png?${p.toString()}`
}
