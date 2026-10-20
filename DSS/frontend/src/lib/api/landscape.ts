// Ландшафтное планирование: клиент к нашему бэкенду /api/v1/landscape/*
// (прокси к специализированным ручкам /landscape/* GIS-сервиса + растры через titiler).
// Чтение — любая роль; замена входа и перерасчёт — admin / data_uploader.

import api from '@/lib/api'

export interface LandscapeDataset {
  id: string
  dg_id: string
  name: string
  status: string
  start_date: string | null
  end_date: string | null
  size_bytes?: number
  file_type?: string
  created_at: string
  updated_at: string
}

export interface LandscapeSlot {
  code: string
  title?: string
  group_id: string
  latest: LandscapeDataset | null
}

export interface LandscapeResult {
  group: string
  title?: string
  node?: string
  group_id: string
  latest: LandscapeDataset | null
  cog_url: string | null
  sld_url: string | null
}

export interface LandscapeRunNode {
  node_code: string
  stage: number
  status: string
  inputs: Array<{ group: string; dataset_id: string }> | null
  outputs: Array<{ group: string; dataset_id: string }> | null
  error: string | null
}

export interface LandscapeRun {
  id: string
  status: string
  from_stage: number | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  /** Узлы графа — только в GET /landscape/runs/{id}; в списке запусков null. */
  nodes?: LandscapeRunNode[] | null
}

export interface LandscapeDatasetStatus {
  id?: string
  status: string
  parse_error?: string | null
}

export interface RasterIngestStatus {
  status: 'running' | 'done' | 'error' | 'absent'
  downloaded?: number
  total?: number | null
  error?: string | null
}

export async function fetchLandscapeSlots(): Promise<LandscapeSlot[]> {
  const { data } = await api.get<LandscapeSlot[]>('/landscape/slots')
  return data ?? []
}

export async function fetchLandscapeResults(): Promise<LandscapeResult[]> {
  const { data } = await api.get<LandscapeResult[]>('/landscape/results')
  return data ?? []
}

export async function fetchLandscapeRuns(page = 1, pageSize = 20): Promise<LandscapeRun[]> {
  const { data } = await api.get<{ data?: LandscapeRun[] }>('/landscape/runs', {
    params: { page, page_size: pageSize },
  })
  return data?.data ?? []
}

export async function fetchLandscapeRun(id: string): Promise<LandscapeRun> {
  const { data } = await api.get<LandscapeRun>(`/landscape/runs/${id}`)
  return data
}

/** Обычный перерасчёт ({}) или полный с первого этапа ({"from_stage": 0}). */
export async function startLandscapeRun(full = false): Promise<LandscapeRun> {
  const { data } = await api.post<LandscapeRun>('/landscape/runs', full ? { from_stage: 0 } : {})
  return data
}

/**
 * Замена входного GeoTIFF слота: новая версия в той же группе. Имя датасета
 * подставляет бэкенд. Период обязателен — по end_date сервер выбирает
 * актуальную версию входа.
 */
export async function replaceLandscapeSlot(
  code: string,
  file: File,
  opts: { startDate: string; endDate: string },
  onProgress?: (pct: number) => void,
): Promise<LandscapeDataset> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('start_date', opts.startDate)
  fd.append('end_date', opts.endDate)
  const { data } = await api.post<LandscapeDataset>(`/landscape/slots/${code}`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: onProgress
      ? (e) => {
          if (e.total) onProgress(Math.round((e.loaded / e.total) * 100))
        }
      : undefined,
  })
  return data
}

export async function fetchLandscapeDatasetStatus(id: string): Promise<LandscapeDatasetStatus> {
  const { data } = await api.get<LandscapeDatasetStatus>(`/landscape/datasets/${id}/status`)
  return data
}

/** SLD слоя; null, если у слоя в ГИС нет стиля (404) — тогда шкала строится по данным. */
export async function fetchLandscapeSld(sldUrl: string): Promise<string | null> {
  try {
    const { data } = await api.get<string>('/landscape/sld', {
      params: { url: sldUrl },
      responseType: 'text',
    })
    return data
  } catch (e) {
    if ((e as { response?: { status?: number } })?.response?.status === 404) return null
    throw e
  }
}

/** Эффективный SLD текущего датасета входа: сервер сам отдаёт групповой, если своего нет. */
export function datasetSldUrl(datasetId: string): string {
  return `/datasets/${datasetId}/sld`
}

// ── Растры на карте ───────────────────────────────────────────────────────────

export async function landscapeRasterIngest(id: string): Promise<RasterIngestStatus> {
  const { data } = await api.post<RasterIngestStatus>(`/landscape/raster/${id}/ingest`)
  return data
}

export async function landscapeRasterIngestStatus(id: string): Promise<RasterIngestStatus> {
  const { data } = await api.get<RasterIngestStatus>(`/landscape/raster/${id}/ingest/status`)
  return data
}

/** Залить COG в том titiler и дождаться готовности (повторно — мгновенно). */
export async function waitRasterIngest(id: string, isStale: () => boolean = () => false): Promise<void> {
  let st = await landscapeRasterIngest(id)
  while (st.status === 'running') {
    await new Promise((r) => setTimeout(r, 1500))
    if (isStale()) return
    st = await landscapeRasterIngestStatus(id)
  }
  if (st.status !== 'done') throw new Error(st.error || 'Не удалось подготовить растр к отображению')
}

/** Диапазон значений растра (titiler statistics) и признак целочисленности. */
export interface LandscapeRasterStats {
  min: number
  max: number
  integer: boolean
}

export async function landscapeRasterStatistics(id: string): Promise<LandscapeRasterStats> {
  const { data } = await api.get<Record<string, { min: number; max: number }>>(
    `/landscape/raster/${id}/statistics`,
  )
  const band = Object.values(data)[0]
  return {
    min: band.min,
    max: band.max,
    integer: Number.isInteger(band.min) && Number.isInteger(band.max),
  }
}

export async function landscapeRasterPoint(id: string, lon: number, lat: number): Promise<number | null> {
  try {
    const { data } = await api.get<{ values?: number[] }>(`/landscape/raster/${id}/point`, {
      params: { lon, lat },
    })
    return data.values?.[0] ?? null
  } catch {
    return null
  }
}

function apiBase(): string {
  return (process.env.NEXT_PUBLIC_API_URL ?? '/api/v1').replace(/\/$/, '')
}

/** Шаблон tile-URL для maplibre: дискретная палитра классов либо colormap_name+rescale. */
export function landscapeTileTemplate(
  id: string,
  opts: { colormap?: Record<string, string>; colormapName?: string; rescale?: string },
): string {
  const p = new URLSearchParams()
  if (opts.colormap) p.set('colormap', JSON.stringify(opts.colormap))
  else p.set('colormap_name', opts.colormapName ?? 'viridis')
  if (opts.rescale) p.set('rescale', opts.rescale)
  return `${apiBase()}/landscape/raster/${id}/tiles/{z}/{x}/{y}.png?${p.toString()}`
}

/** Скачивание COG: файл отдаётся с авторизацией, поэтому через blob. */
export async function downloadLandscapeRaster(id: string, filename: string): Promise<void> {
  const { data } = await api.get<Blob>(`/landscape/raster/${id}/download`, {
    params: { filename },
    responseType: 'blob',
  })
  const url = URL.createObjectURL(data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.toLowerCase().endsWith('.tif') ? filename : `${filename}.tif`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// ── Статус расчёта для пользователя: 4 состояния (ТЗ §5.4) ────────────────────

export type RunPhase = 'started' | 'running' | 'done' | 'failed' | 'unknown'

export function runPhase(status: string | null | undefined): RunPhase {
  const s = (status ?? '').toLowerCase()
  if (['queued', 'pending', 'created', 'scheduled', 'new'].includes(s)) return 'started'
  if (['running', 'processing', 'in_progress'].includes(s)) return 'running'
  if (['succeeded', 'success', 'completed', 'done'].includes(s)) return 'done'
  if (['failed', 'error', 'cancelled', 'canceled'].includes(s)) return 'failed'
  return 'unknown'
}

export const RUN_PHASE_LABEL: Record<RunPhase, string> = {
  started: 'Запущен',
  running: 'Выполняется',
  done: 'Выполнен',
  failed: 'Ошибка',
  unknown: 'Неизвестно',
}

/** Статус узла графа для пользователя; skipped — результат переиспользован, не ошибка. */
export function nodeStatusLabel(status: string): string {
  const s = status.toLowerCase()
  if (s === 'skipped') return 'переиспользован'
  const ph = runPhase(s)
  return ph === 'unknown' ? status : RUN_PHASE_LABEL[ph].toLowerCase()
}

/** Прогресс запуска по узлам: завершено (включая переиспользованные) из всего. */
export function runProgress(run: LandscapeRun): { done: number; total: number; skipped: number } | null {
  if (!run.nodes || run.nodes.length === 0) return null
  const skipped = run.nodes.filter((n) => n.status === 'skipped').length
  const done = run.nodes.filter((n) => n.status === 'skipped' || runPhase(n.status) === 'done').length
  return { done, total: run.nodes.length, skipped }
}

export function isRunActive(status: string | null | undefined): boolean {
  const ph = runPhase(status)
  return ph === 'started' || ph === 'running'
}

/** Текст ошибки API для пользователя. */
export function apiErrorMessage(e: unknown, fallback: string): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const err = e as any
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg)
  if (err?.response?.status === 403) return 'Недостаточно прав для этого действия'
  if (err?.message === 'Network Error') return 'Нет связи с сервером'
  return fallback
}

// ── Связь вариантов ЛП со сценариями развития ─────────────────────────────────

export interface LandscapeZoning {
  variant: string
  label: string
  dataset_id: string
  result_updated_at?: string | null
  legend: Array<{ value: number; label: string; color: string | null }>
  /** Доли зон по области, % (ключ — значение класса). */
  oblast_pct: Record<string, number>
  districts: Array<{ kato: string; name: string; pct: Record<string, number> }>
  method?: string
}

/** Снимок варианта в параметрах сценария (фиксируется при утверждении). */
export interface ScenarioLandscapeSnapshot extends LandscapeZoning {
  linked_at?: string
  linked_by?: string | null
  kpi_effect?: { status: string; reason?: string }
}

/** Доли функциональных зон варианта по области и районам (первый расчёт ~15 с). */
export async function fetchLandscapeZoning(group: string): Promise<LandscapeZoning> {
  const { data } = await api.get<LandscapeZoning>(`/landscape/zoning/${group}`)
  return data
}

/** Привязать к сценарию вариант ЛП; снимок зонирования считает сервер. */
export async function setScenarioLandscapeVariant(scenarioId: string, variant: string): Promise<void> {
  await api.put(`/landscape/scenarios/${scenarioId}/variant`, { variant })
}

// ── Многокритериальное сравнение вариантов (GET /landscape/compare) ─────────

export type CompareVariant = 'V1' | 'V2' | 'V3'
export type CompareBlock = 'nature' | 'economy' | 'feasibility'

export interface CompareCriterion {
  id: string
  block: CompareBlock
  better: 'lower' | 'higher'
  label: string
  source: string
}

export type CompareValues = Record<CompareVariant, Record<string, number>>

export interface LandscapeCompare {
  built_at: string
  unit: string
  variants: Record<CompareVariant, { group: string; label: string; dataset_id: string }>
  blocks: Record<CompareBlock, string>
  criteria: CompareCriterion[]
  historical_max_change_per_year: number
  oblast: CompareValues
  territories: { kato: string; name: string; values: CompareValues }[]
  sources: string[]
  limitations: string[]
  freshness: { stale: boolean | null; current: Partial<Record<CompareVariant, string | null>> }
}

export async function fetchLandscapeCompare(): Promise<LandscapeCompare> {
  const { data } = await api.get<LandscapeCompare>('/landscape/compare')
  return data
}
