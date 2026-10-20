import api from '@/lib/api'
import type { Direction } from '@/types/direction'

export interface Region {
  id: string
  name: string
  code: string
  level: number
}

export interface DataLayer {
  id: string
  name: string
  description: string
  category_name: string
  direction_id: string
  direction_name: string
  unit: string
  data_type: string
}

export interface SpatialDataProperties {
  value: number
  unit: string
  year: number
}

export interface SpatialDataItem {
  id: string
  layer_id: string
  layer_name: string
  region_id: string
  region_name: string
  data_date: string
  properties: SpatialDataProperties
}

export interface SdMonitoringMetrics {
  total_sources: number
  active_sources: number
  total_categories: number
  total_layers: number
  active_layers: number
  total_spatial_records: number
  pending_validation: number
  approved_records: number
  rejected_records: number
  total_annotations: number
}

export interface GeoJSONFeature {
  type: 'Feature'
  geometry: {
    type: string
    coordinates: unknown
  }
  properties: Record<string, unknown>
}

export interface GeoJSONFeatureCollection {
  type: 'FeatureCollection'
  features: GeoJSONFeature[]
}

interface SpatialDataParams {
  layer_id?: string
  region_id?: string
  date_from?: string
  date_to?: string
  page_size?: number
}

interface GeoJSONParams {
  layer_id?: string
  region_id?: string
  date_from?: string
  date_to?: string
}

interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface UserMinimal {
  id: string
  username: string
}

// Дедуп: регионы — статический справочник в рамках сессии. Дашборды рендерят
// несколько панелей (IndicatorView), каждая дёргает fetchRegions в useEffect →
// без кэша это N одинаковых запросов. Кэшируем промис; при ошибке сбрасываем,
// чтобы сбой не «отравлял» кэш.
let regionsCache: Promise<Region[]> | null = null

async function _fetchRegions(): Promise<Region[]> {
  try {
    const { data } = await api.get<Region[]>('/regions')
    return data
  } catch {
    return []
  }
}

export function fetchRegions(): Promise<Region[]> {
  if (!regionsCache) {
    regionsCache = _fetchRegions().then((data) => {
      // Пустой результат = ошибка/нет данных: не кэшируем, чтобы следующий вызов повторил.
      if (data.length === 0) regionsCache = null
      return data
    })
  }
  return regionsCache
}

export async function fetchUsersByRegion(region_id: string): Promise<UserMinimal[]> {
  try {
    const { data } = await api.get<UserMinimal[]>('/users/by-region', { params: { region_id } })
    return data
  } catch {
    return []
  }
}

export async function fetchDirections(): Promise<Direction[]> {
  try {
    const { data } = await api.get<Direction[]>('/directions')
    return data
  } catch {
    return []
  }
}

export async function assignUserDirections(userId: string, directionIds: string[]): Promise<void> {
  await api.put(`/users/${userId}/directions`, { direction_ids: directionIds })
}

export async function fetchDataLayers(): Promise<DataLayer[]> {
  try {
    const { data } = await api.get<PaginatedResponse<DataLayer>>('/data-layers', {
      params: { page: 1, page_size: 100 },
    })
    return data.items ?? (data as unknown as DataLayer[])
  } catch {
    return []
  }
}

export async function fetchSpatialData(params: SpatialDataParams): Promise<SpatialDataItem[]> {
  try {
    const query: Record<string, string | number> = {
      validation_status: 'approved',
      page_size: params.page_size ?? 200,
    }
    if (params.layer_id) query.layer_id = params.layer_id
    if (params.region_id) query.region_id = params.region_id
    if (params.date_from) query.date_from = params.date_from
    if (params.date_to) query.date_to = params.date_to

    const { data } = await api.get<PaginatedResponse<SpatialDataItem>>('/spatial-data', {
      params: query,
    })
    return data.items ?? (data as unknown as SpatialDataItem[])
  } catch {
    return []
  }
}

export async function fetchGeoJSON(params: GeoJSONParams): Promise<GeoJSONFeatureCollection> {
  try {
    const query: Record<string, string> = {}
    if (params.layer_id) query.layer_id = params.layer_id
    if (params.region_id) query.region_id = params.region_id
    if (params.date_from) query.date_from = params.date_from
    if (params.date_to) query.date_to = params.date_to

    const { data } = await api.get<GeoJSONFeatureCollection>('/spatial-data/geojson', {
      params: query,
    })
    return data
  } catch {
    return { type: 'FeatureCollection', features: [] }
  }
}

export interface ShapefileInfo {
  name: string
  display_name: string
}

export async function fetchShapefileList(): Promise<ShapefileInfo[]> {
  try {
    const { data } = await api.get<ShapefileInfo[]>('/shapefiles')
    return data
  } catch {
    return []
  }
}

export async function fetchShapefileGeoJSON(name: string): Promise<GeoJSONFeatureCollection> {
  try {
    const { data } = await api.get<GeoJSONFeatureCollection>('/shapefiles/geojson', { params: { name } })
    return data
  } catch {
    return { type: 'FeatureCollection', features: [] }
  }
}

// Границы районов СКО — выделенный статический GeoJSON-ассет (public/geo/districts.geojson).
// Свойства feature: kato, name_ru, name_kk, name_en. Джойн данных к геометрии — по kato
// (см. fetchKatoDistricts). Дедуп: одинаковы для всех панелей дашборда, но каждая
// IndicatorView запрашивает их в useEffect → кэшируем промис на сессию; пустой результат
// (ошибка) не кэшируем, чтобы следующий вызов повторил.
let boundariesCache: Promise<GeoJSONFeatureCollection> | null = null

export function fetchDistrictBoundaries(): Promise<GeoJSONFeatureCollection> {
  if (!boundariesCache) {
    boundariesCache = fetch('/geo/districts.geojson')
      .then((r) => (r.ok ? (r.json() as Promise<GeoJSONFeatureCollection>) : { type: 'FeatureCollection' as const, features: [] }))
      .then((fc) => {
        if (!fc.features || fc.features.length === 0) boundariesCache = null
        return fc
      })
      .catch(() => {
        boundariesCache = null
        return { type: 'FeatureCollection' as const, features: [] }
      })
  }
  return boundariesCache
}

// Джойн границ районов к данным идёт по КАТО-коду: feature.properties.kato →
// kato_reference.region_id → regions.id (тот же путь, что и импорт данных,
// backend/app/services/intake_service.py). GET /kato?level=2 отдаёт все район-строки с
// region_id. Кэшируем промис на сессию; пустую/ошибочную мапу не кэшируем.
let katoDistrictsCache: Promise<Map<string, string>> | null = null

export function fetchKatoDistricts(): Promise<Map<string, string>> {
  if (!katoDistrictsCache) {
    katoDistrictsCache = api
      .get<KatoListResponse>('/kato', { params: { level: 2 } })
      .then(({ data }) => {
        const map = new Map<string, string>()
        for (const k of data.items ?? []) {
          if (k.region_id) map.set(k.kato_code, k.region_id)
        }
        if (map.size === 0) katoDistrictsCache = null
        return map
      })
      .catch(() => {
        katoDistrictsCache = null
        return new Map<string, string>()
      })
  }
  return katoDistrictsCache
}

// ─── KATO Reference CRUD ─────────────────────────────────────────────────────

export interface KatoItem {
  kato_code: string
  name_ru: string
  name_kz: string | null
  type_code: string | null
  type_name_ru: string | null
  parent_code: string | null
  level: number
  is_active: boolean
  region_id: string | null
  created_at: string
}

export interface KatoListResponse {
  items: KatoItem[]
  total: number
}

interface KatoListParams {
  level?: number
  search?: string
  code_search?: string
  is_active?: boolean
}

export async function fetchKatoList(params?: KatoListParams): Promise<KatoListResponse> {
  try {
    const query: Record<string, string | number | boolean> = {}
    if (params?.level !== undefined) query.level = params.level
    if (params?.search) query.search = params.search
    if (params?.code_search) query.code_search = params.code_search
    if (params?.is_active !== undefined) query.is_active = params.is_active
    const { data } = await api.get<KatoListResponse>('/kato', { params: query })
    return data
  } catch {
    return { items: [], total: 0 }
  }
}

export interface KatoCreate {
  kato_code: string
  name_ru: string
  name_kz?: string
  type_code?: string
  type_name_ru?: string
  parent_code?: string
  level: number
  is_active?: boolean
  region_id?: string | null
}

export interface KatoUpdate {
  name_ru?: string
  name_kz?: string | null
  type_code?: string | null
  type_name_ru?: string | null
  parent_code?: string | null
  level?: number
  is_active?: boolean
  region_id?: string | null
}

export async function createKato(body: KatoCreate): Promise<KatoItem> {
  const { data } = await api.post<KatoItem>('/kato', body)
  return data
}

export async function updateKato(kato_code: string, body: KatoUpdate): Promise<KatoItem> {
  const { data } = await api.patch<KatoItem>(`/kato/${kato_code}`, body)
  return data
}

export async function deleteKato(kato_code: string): Promise<void> {
  await api.delete(`/kato/${kato_code}`)
}

export interface KatoImportStatus {
  status: 'running' | 'done' | 'error'
  total: number
  processed: number
  added: number
  updated: number
  skipped: number
  errors: string[]
  error_message?: string
}

export async function importKatoCsv(file: File): Promise<{ job_id: string }> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<{ job_id: string }>('/kato/import', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function getKatoImportStatus(jobId: string): Promise<KatoImportStatus> {
  const { data } = await api.get<KatoImportStatus>(`/kato/import/${jobId}`)
  return data
}

// ─── Units (единицы измерения) ───────────────────────────────────────────────

export interface Unit {
  id: string
  name: string
  description: string | null
  is_active: boolean
  created_at: string
}

export interface UnitCreate {
  name: string
  description?: string | null
  is_active?: boolean
}

export interface UnitUpdate {
  name?: string
  description?: string | null
  is_active?: boolean
}

export async function fetchUnits(params?: { is_active?: boolean }): Promise<Unit[]> {
  try {
    const { data } = await api.get<Unit[]>('/units', { params })
    return data
  } catch {
    return []
  }
}

export async function createUnit(body: UnitCreate): Promise<Unit> {
  const { data } = await api.post<Unit>('/units', body)
  return data
}

export async function updateUnit(id: string, body: UnitUpdate): Promise<Unit> {
  const { data } = await api.patch<Unit>(`/units/${id}`, body)
  return data
}

export async function deleteUnit(id: string): Promise<void> {
  await api.delete(`/units/${id}`)
}

// ─── Economic sectors (справочник секторов экономики, ОКЭД) ───────────────────

export interface EconomicSector {
  id: string
  oked_code: string
  /** Полное имя сектора (как в источнике). */
  name: string
  /** Короткая подпись для графиков. */
  short_name: string
  /** Имя показателя ЗП в каталоге indicators — связь сектор → показатель по имени. */
  indicator_name: string
  /** Цвет серии, #RRGGBB. */
  color: string
  /** true только для «Промышленность» (агрегат B–E) — не суммировать с под-секторами. */
  is_aggregate: boolean
  /** ОКЭД родителя-агрегата (для B/C/D/E → 'B-E'). */
  parent_code: string | null
  sort_order: number
  is_active: boolean
  created_at: string
}

export async function fetchEconomicSectors(
  params?: { is_active?: boolean },
): Promise<EconomicSector[]> {
  try {
    const { data } = await api.get<EconomicSector[]>('/economic-sectors', { params })
    return data
  } catch {
    return []
  }
}

// ─── Meteo stations (метеостанции СКО) ────────────────────────────────────────

export interface MeteoStation {
  id: string
  /** Название станции. */
  name: string
  /** Район СКО (regions.id), к которому привязана станция. */
  region_id: string
  /** КАТО-код населённого пункта станции (опц.). */
  kato_code: string | null
  /** Координаты WGS84 (опц.). */
  lat: number | null
  lon: number | null
  is_active: boolean
  created_at: string
}

export async function fetchMeteoStations(
  params?: { is_active?: boolean; region_id?: string },
): Promise<MeteoStation[]> {
  try {
    const { data } = await api.get<MeteoStation[]>('/meteo-stations', { params })
    return data
  } catch {
    return []
  }
}

// ─── Indicators (показатели направлений) ─────────────────────────────────────

export interface Indicator {
  id: string
  direction_id: string
  direction_name: string | null
  unit_id: string
  unit_name: string | null
  name: string
  sort_order: number
  is_active: boolean
  created_at: string
}

export interface IndicatorCreate {
  direction_id: string
  unit_id: string
  name: string
  sort_order?: number
  is_active?: boolean
}

export interface IndicatorUpdate {
  direction_id?: string
  unit_id?: string
  name?: string
  sort_order?: number
  is_active?: boolean
}

export async function fetchIndicators(params?: {
  direction_id?: string
  is_active?: boolean
  search?: string
}): Promise<Indicator[]> {
  try {
    const { data } = await api.get<Indicator[]>('/indicators', { params })
    return data
  } catch {
    return []
  }
}

export async function createIndicator(body: IndicatorCreate): Promise<Indicator> {
  const { data } = await api.post<Indicator>('/indicators', body)
  return data
}

export async function updateIndicator(id: string, body: IndicatorUpdate): Promise<Indicator> {
  const { data } = await api.patch<Indicator>(`/indicators/${id}`, body)
  return data
}

export async function deleteIndicator(id: string): Promise<void> {
  await api.delete(`/indicators/${id}`)
}

// ── IndicatorValue (time-series for dashboards) ─────────────────────────

export interface IndicatorValuePoint {
  period_start: string
  period_end: string
  value: number
  validation_status: 'pending' | 'approved' | 'rejected'
}

export interface IndicatorTimeSeries {
  indicator_id: string
  indicator_name: string | null
  region_id: string
  region_name: string | null
  period_type: string
  points: IndicatorValuePoint[]
}

export interface IndicatorValuesListItem {
  id: string
  indicator_id: string
  indicator_name: string | null
  direction_id: string | null
  region_id: string
  region_name: string | null
  period_type: string
  period_start: string
  period_end: string
  value: number
  validation_status: 'pending' | 'approved' | 'rejected'
  created_at: string
}

export async function fetchIndicatorTimeSeries(params: {
  indicator_id: string
  region_id: string
  period_type?: 'year' | 'quarter' | 'month' | 'day'
  date_from?: string
  date_to?: string
  only_approved?: boolean
}): Promise<IndicatorTimeSeries> {
  const { data } = await api.get<IndicatorTimeSeries>(
    '/indicator-values/timeseries',
    {
      params: {
        indicator_id: params.indicator_id,
        region_id: params.region_id,
        period_type: params.period_type ?? 'year',
        date_from: params.date_from,
        date_to: params.date_to,
        only_approved: params.only_approved ?? false,
      },
    }
  )
  return data
}

export async function fetchIndicatorValues(params: {
  direction_id?: string
  indicator_id?: string
  region_id?: string
  period_type?: string
  date_from?: string
  date_to?: string
  validation_status?: 'pending' | 'approved' | 'rejected'
  page?: number
  page_size?: number
}): Promise<{ items: IndicatorValuesListItem[]; total: number; pages: number }> {
  try {
    const { data } = await api.get<{
      items: IndicatorValuesListItem[]
      total: number
      page: number
      page_size: number
      pages: number
    }>('/indicator-values', {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 100,
        ...params,
      },
    })
    return { items: data.items, total: data.total, pages: data.pages }
  } catch {
    return { items: [], total: 0, pages: 0 }
  }
}

// Отчётный год дашбордов: все экраны показывают данные не позже этого года.
// В БД и на экранах управления данными значения остаются как загружены.
export const DATA_YEAR = 2025

// Приведение к отчётному году (только отображение). Значение с периодом позже
// DATA_YEAR показывается как значение за DATA_YEAR, если у показателя по этому
// региону нет собственного значения за DATA_YEAR (статичные характеристики,
// загруженные текущим годом: водный фонд, расстояния, угодья); иначе скрывается.
export function capToDataYear<T extends IndicatorValuesListItem>(items: T[]): T[] {
  const yearOf = (v: T) => Number(v.period_start.slice(0, 4))
  const key = (v: T) => `${v.indicator_id}|${v.region_id}`
  const hasDataYear = new Set<string>()
  for (const v of items) if (yearOf(v) === DATA_YEAR) hasDataYear.add(key(v))
  const later = new Map<string, T>()
  const out: T[] = []
  for (const v of items) {
    if (yearOf(v) <= DATA_YEAR) {
      out.push(v)
      continue
    }
    // Подгодовые значения (месяцы, декады) после отчётного года не показываются:
    // переносить месяц другого года в отчётный нельзя.
    if (v.period_type !== 'year') continue
    const k = key(v)
    if (hasDataYear.has(k)) continue
    const cur = later.get(k)
    if (!cur || v.period_start > cur.period_start) later.set(k, v)
  }
  const redate = (d: string) => `${DATA_YEAR}${d.slice(4)}`
  later.forEach((v) => {
    out.push({ ...v, period_start: redate(v.period_start), period_end: redate(v.period_end) })
  })
  return out
}

// Одобренные значения целиком (все страницы) для дашбордов, приведённые к
// отчётному году (см. capToDataYear).
export async function fetchApprovedValues(params: {
  direction_id?: string
  indicator_id?: string
  period_type?: string
  maxPages?: number
  /** false — без приведения к отчётному году (вызывающий код приводит сам). */
  cap?: boolean
}): Promise<IndicatorValuesListItem[]> {
  const { maxPages = 20, cap = true, ...filter } = params
  const all: IndicatorValuesListItem[] = []
  for (let page = 1; page <= maxPages; page += 1) {
    const r = await fetchIndicatorValues({
      ...filter,
      validation_status: 'approved',
      page,
      page_size: 1000,
    })
    all.push(...r.items)
    if (page >= r.pages || r.items.length < 1000) break
  }
  return cap ? capToDataYear(all) : all
}

// ── IndicatorNorm (среднемноголетняя норма для линии нормы на графиках) ──

export interface IndicatorNorm {
  id: string
  indicator_id: string
  indicator_name: string | null
  region_id: string
  region_name: string | null
  norm_value: number
  period_label: string
  method: string
  ref_period_start: string | null
  ref_period_end: string | null
  note: string | null
}

export async function fetchIndicatorNorms(params: {
  indicator_id?: string
  region_id?: string
  direction_id?: string
}): Promise<IndicatorNorm[]> {
  try {
    const { data } = await api.get<IndicatorNorm[]>('/indicator-norms', { params })
    return data
  } catch {
    return []
  }
}

export interface SpatialRecord {
  id: string
  layer_id: string
  layer_name: string | null
  region_id: string | null
  region_name: string | null
  data_date: string
  validation_status: 'pending' | 'approved' | 'rejected'
  validated_by_username: string | null
  validated_at: string | null
  properties: Record<string, unknown> | null
  created_at: string
}

interface SpatialRecordsParams {
  validation_status?: 'pending' | 'approved' | 'rejected'
  layer_id?: string
  page?: number
  page_size?: number
}

export async function fetchSpatialRecords(params?: SpatialRecordsParams): Promise<{ items: SpatialRecord[]; total: number }> {
  try {
    const query: Record<string, string | number> = { page: params?.page ?? 1, page_size: params?.page_size ?? 50 }
    if (params?.validation_status) query.validation_status = params.validation_status
    if (params?.layer_id) query.layer_id = params.layer_id
    const { data } = await api.get<{ items: SpatialRecord[]; total: number }>('/spatial-data', { params: query })
    return data
  } catch {
    return { items: [], total: 0 }
  }
}

export async function uploadSpatialData(body: {
  layer_id: string
  region_id?: string
  data_date: string
  geojson?: object
  properties?: Record<string, unknown>
}): Promise<SpatialRecord> {
  const { data } = await api.post<SpatialRecord>('/spatial-data', body)
  return data
}

export async function validateSpatialData(id: string, status: 'approved' | 'rejected', comment?: string): Promise<SpatialRecord> {
  const { data } = await api.post<SpatialRecord>(`/spatial-data/${id}/validate`, { status, comment })
  return data
}

export async function fetchSdMetrics(): Promise<SdMonitoringMetrics> {
  try {
    const { data } = await api.get<SdMonitoringMetrics>('/monitoring/sdh')
    return data
  } catch {
    return {
      total_sources: 0,
      active_sources: 0,
      total_categories: 0,
      total_layers: 0,
      active_layers: 0,
      total_spatial_records: 0,
      pending_validation: 0,
      approved_records: 0,
      rejected_records: 0,
      total_annotations: 0,
    }
  }
}

// ── Системный мониторинг (ФFE.7): статус служб, размер БД, история прогнозов ──

export interface ServiceStatus {
  name: string
  status: 'up' | 'down'
  latency_ms?: number | null
  detail?: string | null
}

export interface SystemMetrics {
  services: ServiceStatus[]
  db_size_bytes: number
  db_size_pretty: string
  spatial_table_bytes: number
}

export interface ForecastRun {
  id: string
  indicator_id?: string | null
  region_id?: string | null
  forecast_date?: string | null
  status: string
  created_at?: string | null
}

export async function fetchSystemMetrics(): Promise<SystemMetrics> {
  const { data } = await api.get<SystemMetrics>('/monitoring/system')
  return data
}

// Ресурсы сервера из Prometheus (ЦП, память, диск, контейнеры Системы) и графики.
export interface ServerResources {
  available: boolean
  detail?: string
  hours?: number
  current?: { cpu: number | null; memory: number | null; disk: number | null }
  series?: { cpu: [number, number][]; memory: [number, number][]; disk: [number, number][] }
  containers?: { name: string; cpu_percent: number; memory_mb: number }[]
}

export async function fetchServerResources(hours = 24): Promise<ServerResources> {
  const { data } = await api.get<ServerResources>('/monitoring/resources', { params: { hours } })
  return data
}

// Журнал ошибок модуля: backend (BackEnd и СХД) или ai-service (Модуль ИИ).
export interface ModuleError {
  time: string
  level: string
  logger: string
  message: string
  trace?: string
}

export async function fetchModuleErrors(module: 'backend' | 'ai-service', limit = 50): Promise<ModuleError[]> {
  const { data } = await api.get<ModuleError[]>('/monitoring/errors', { params: { module, limit } })
  return data
}

// Журнал аудита целиком по фильтру — CSV с сервера (а не только текущая страница).
export async function exportAuditLog(params: { action?: string }): Promise<Blob> {
  const { data } = await api.get<Blob>('/audit-log/export', { params, responseType: 'blob' })
  return data
}

export async function fetchForecastHistory(limit = 20): Promise<ForecastRun[]> {
  const { data } = await api.get<ForecastRun[]>('/monitoring/forecasts', { params: { limit } })
  return data
}

// ── Журнал аудита (G3, ADM) ──

export interface AuditLogItem {
  id: string
  user_id?: string | null
  username?: string | null
  action: string
  entity_type: string
  entity_id?: string | null
  details?: Record<string, unknown> | null
  ip_address?: string | null
  created_at: string
}

export interface AuditLogPage {
  items: AuditLogItem[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface AuditLogFilters {
  action?: string
  entity_type?: string
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}

export async function fetchAuditLog(filters: AuditLogFilters = {}): Promise<AuditLogPage> {
  const { data } = await api.get<AuditLogPage>('/audit-log', { params: filters })
  return data
}

export async function fetchAuditActions(): Promise<string[]> {
  const { data } = await api.get<string[]>('/audit-log/actions')
  return data
}

// ── Аннотации на карте (G2, EXP_VER/EXP_DIR) ──

export interface MapAnnotation {
  id: string
  comment: string
  longitude?: number | null
  latitude?: number | null
  layer_id?: string | null
  spatial_data_id?: string | null
  indicator_value_id?: string | null
  author_id: string
  author_name?: string | null
  created_at?: string | null
}

export async function createAnnotation(body: {
  comment: string
  longitude?: number
  latitude?: number
  layer_id?: string | null
  spatial_data_id?: string | null
  indicator_value_id?: string | null
}): Promise<MapAnnotation> {
  const { data } = await api.post<MapAnnotation>('/annotations', body)
  return data
}

export async function fetchAnnotations(params: {
  layerId?: string
  indicatorValueIds?: string[]
  onMap?: boolean
} = {}): Promise<MapAnnotation[]> {
  const { data } = await api.get<MapAnnotation[]>('/annotations', {
    params: {
      layer_id: params.layerId,
      indicator_value_ids: params.indicatorValueIds?.length ? params.indicatorValueIds.join(',') : undefined,
      on_map: params.onMap || undefined,
    },
  })
  return data
}

export async function deleteAnnotation(id: string): Promise<void> {
  await api.delete(`/annotations/${id}`)
}

// ─────────────────────────────────────────────────────────────────────────────
// Каталог геоданных (реальный сервис dssgis.dwh.kz).
// Все вызовы идут через прокси DSS-бэкенда /api/v1/gis/* → GIS-сервис.
// Категория GIS = наше направление, датасет GIS = наш показатель (см. /gis-links).
// ─────────────────────────────────────────────────────────────────────────────

export type GisFileType = 'vector' | 'vector_with_kato' | 'raster'

export interface GisCategory {
  id: string
  parent_id?: string | null
  code?: string | null
  name: string
  description?: string | null
  created_at?: string
  updated_at?: string
}

// Элемент списка GET /datasets.
export interface GisDataset {
  id: string
  category_id: string
  name: string
  description?: string | null
  unit?: string | null
  file_type: GisFileType
  size_bytes?: number | null
  status: string
  created_at?: string
  updated_at?: string
  dg_id?: string | null
  // Период, который описывает датасет (YYYY-MM-DD, end_date включительно).
  // Заполнены не у всех датасетов — у части растров и векторов обе даты null.
  start_date?: string | null
  end_date?: string | null
}

export interface GisAttributeColumn {
  name: string
  samples?: (string | number | null)[]
}

// Колонка-год маппинга: точка во времени (start_date) или период (start_date..end_date).
export interface GisYearColumn {
  column: string
  start_date: string
  end_date?: string | null
}

// Карточка GET /datasets/{id}.
export interface GisDatasetDetail extends GisDataset {
  filename?: string | null
  storage_key?: string | null
  cog_storage_key?: string | null
  content_type?: string | null
  meta?: Record<string, unknown> | null
  properties?: Record<string, unknown> | null
  automated?: boolean
  attribute_columns?: GisAttributeColumn[]
  kato_column?: string | null
  year_columns?: GisYearColumn[]
  parse_error?: string | null
  geometry?: GeoJsonGeometry | null
  bbox?: number[] | null
}

export interface GisDatasetStatus {
  id: string
  status: string
  parse_error?: string | null
}

export interface GisObservation {
  id: string
  dataset_id: string
  kato_code: string
  start_date: string
  end_date?: string | null
  value: number | null
  value_text?: string | null
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type GeoJsonGeometry = any
export interface GeoJsonFeatureCollection {
  type: 'FeatureCollection'
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  features: any[]
}

// Пагинированный ответ реального GIS API: { data, page, page_size, total, total_pages }.
export interface GisPage<T> {
  data: T[]
  page: number
  page_size: number
  total: number
  total_pages: number
}

export interface GisDatasetsParams {
  category_id?: string
  file_type?: GisFileType
  page?: number
  page_size?: number
}

export async function gisListCategories(): Promise<GisCategory[]> {
  const { data } = await api.get('/gis/categories')
  return Array.isArray(data) ? data : (data.data ?? [])
}

export async function gisListDatasets(params?: GisDatasetsParams): Promise<GisPage<GisDataset>> {
  const { data } = await api.get<GisPage<GisDataset>>('/gis/datasets', { params })
  return data
}

// GIS API ограничивает page_size (макс. 100), поэтому для полного каталога грузим
// все страницы. Опциональные фильтры category_id/file_type прокидываются в API.
export async function gisListAllDatasets(
  params?: { category_id?: string; file_type?: GisFileType },
): Promise<GisDataset[]> {
  const first = await gisListDatasets({ ...params, page: 1, page_size: 100 })
  let all = first.data
  for (let p = 2; p <= (first.total_pages ?? 1); p++) {
    const pg = await gisListDatasets({ ...params, page: p, page_size: 100 })
    all = all.concat(pg.data)
  }
  return all
}

export async function gisGetDataset(id: string): Promise<GisDatasetDetail> {
  const { data } = await api.get<GisDatasetDetail>(`/gis/datasets/${id}`)
  return data
}

export async function gisGetDatasetStatus(id: string): Promise<GisDatasetStatus> {
  const { data } = await api.get<GisDatasetStatus>(`/gis/datasets/${id}/status`)
  return data
}

export async function gisGetDatasetGeojson(id: string): Promise<GeoJsonFeatureCollection> {
  const { data } = await api.get<GeoJsonFeatureCollection>(`/gis/datasets/${id}.geojson`)
  return data
}

/** SLD набора (нормализованный бэкендом); null — у набора нет стиля. */
export async function gisGetDatasetSld(id: string): Promise<string | null> {
  try {
    const { data } = await api.get<string>(`/gis/datasets/${id}/sld`, { responseType: 'text' })
    return data
  } catch (e) {
    if ((e as { response?: { status?: number } })?.response?.status === 404) return null
    throw e
  }
}

export async function gisListObservations(
  id: string,
  params?: { page?: number; page_size?: number },
): Promise<GisPage<GisObservation>> {
  const { data } = await api.get<GisPage<GisObservation>>(`/gis/datasets/${id}/observations`, { params })
  return data
}

// Импорт нового слоя: POST /datasets (multipart). Поля собираются вызывающим кодом.
// onProgress — процент байтового аплоада (0..100) для прогресс-бара.
export async function gisCreateDataset(
  fd: FormData,
  onProgress?: (pct: number) => void,
): Promise<GisDatasetDetail> {
  const { data } = await api.post<GisDatasetDetail>('/gis/datasets', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: onProgress
      ? (e) => {
          if (e.total) onProgress(Math.round((e.loaded / e.total) * 100))
        }
      : undefined,
  })
  return data
}

// Маппинг KATO для vector_with_kato: POST /datasets/{id}/mapping.
export async function gisCreateMapping(
  id: string,
  body: { kato_column: string; year_columns: GisYearColumn[] },
): Promise<GisDatasetDetail> {
  const { data } = await api.post<GisDatasetDetail>(`/gis/datasets/${id}/mapping`, body)
  return data
}

// ─────────────────────────────────────────────────────────────────────────────
// Растровые тайлы GIS через titiler (наш бэкенд /api/v1/gis/raster/*).
// info/statistics/point ходят через axios (JWT в заголовке). Тайлы грузит сам
// maplibre по шаблону gisRasterTileTemplate — авторизация через transformRequest.
// ─────────────────────────────────────────────────────────────────────────────

export interface GisRasterStats {
  min: number
  max: number
}

// titiler /statistics возвращает { "b1": { min, max, ... } } (ключ канала).
export async function gisRasterStatistics(id: string): Promise<GisRasterStats | null> {
  try {
    const { data } = await api.get<Record<string, { min: number; max: number }>>(
      `/gis/raster/${id}/statistics`,
    )
    const band = Object.values(data)[0]
    if (!band) return null
    return { min: band.min, max: band.max }
  } catch {
    return null
  }
}

export async function gisRasterInfo(id: string): Promise<Record<string, unknown> | null> {
  try {
    const { data } = await api.get<Record<string, unknown>>(`/gis/raster/${id}/info`)
    return data
  } catch {
    return null
  }
}

export async function gisRasterPoint(
  id: string,
  lon: number,
  lat: number,
): Promise<number | null> {
  try {
    const { data } = await api.get<{ values?: number[] }>(`/gis/raster/${id}/point`, {
      params: { lon, lat },
    })
    return data.values?.[0] ?? null
  } catch {
    return null
  }
}

export interface GisRasterIngestStatus {
  status: 'running' | 'done' | 'error' | 'absent'
  downloaded?: number
  total?: number | null
  error?: string | null
}

// Забрать COG растра из GIS-сервиса в том titiler (по требованию, после UI-загрузки).
export async function gisRasterIngest(
  id: string,
  force = false,
): Promise<GisRasterIngestStatus> {
  const { data } = await api.post<GisRasterIngestStatus>(
    `/gis/raster/${id}/ingest`,
    null,
    { params: force ? { force: true } : undefined },
  )
  return data
}

export async function gisRasterIngestStatus(id: string): Promise<GisRasterIngestStatus> {
  const { data } = await api.get<GisRasterIngestStatus>(`/gis/raster/${id}/ingest/status`)
  return data
}

// Шаблон tile-URL для maplibre raster source (сам добавит {z}/{x}/{y}).
export function gisRasterTileTemplate(
  id: string,
  opts: { colormap?: string; rescale?: string; nodata?: string | number },
): string {
  const base = (process.env.NEXT_PUBLIC_API_URL ?? '/api/v1').replace(/\/$/, '')
  const p = new URLSearchParams()
  p.set('colormap_name', opts.colormap ?? 'viridis')
  if (opts.rescale) p.set('rescale', opts.rescale)
  if (opts.nodata !== undefined && opts.nodata !== null) p.set('nodata', String(opts.nodata))
  return `${base}/gis/raster/${id}/tiles/{z}/{x}/{y}.png?${p.toString()}`
}

// ─────────────────────────────────────────────────────────────────────────────
// Связи каталога GIS с доменом DSS (наш бэкенд /api/v1/gis-links/*).
// Категория GIS → направление, датасет GIS → показатель. Upsert идемпотентен.
// ─────────────────────────────────────────────────────────────────────────────

export interface GisCategoryLink {
  id: string
  gis_category_id: string
  gis_category_code?: string | null
  gis_category_name?: string | null
  direction_id: string
  direction_name?: string | null
  created_at: string
  updated_at: string
}

export interface GisDatasetLink {
  id: string
  gis_dataset_id: string
  gis_dataset_name?: string | null
  gis_category_id?: string | null
  indicator_id: string
  indicator_name?: string | null
  direction_id?: string | null
  created_at: string
  updated_at: string
}

export async function gisListCategoryLinks(): Promise<GisCategoryLink[]> {
  try {
    const { data } = await api.get<GisCategoryLink[]>('/gis-links/categories')
    return data
  } catch {
    return []
  }
}

export async function gisUpsertCategoryLink(body: {
  gis_category_id: string
  direction_id: string
  gis_category_code?: string | null
  gis_category_name?: string | null
}): Promise<GisCategoryLink> {
  const { data } = await api.put<GisCategoryLink>('/gis-links/categories', body)
  return data
}

export async function gisDeleteCategoryLink(gisCategoryId: string): Promise<void> {
  await api.delete(`/gis-links/categories/${gisCategoryId}`)
}

export async function gisListDatasetLinks(gisCategoryId?: string): Promise<GisDatasetLink[]> {
  try {
    const { data } = await api.get<GisDatasetLink[]>('/gis-links/datasets', {
      params: gisCategoryId ? { gis_category_id: gisCategoryId } : undefined,
    })
    return data
  } catch {
    return []
  }
}

export async function gisUpsertDatasetLink(body: {
  gis_dataset_id: string
  indicator_id: string
  gis_dataset_name?: string | null
  gis_category_id?: string | null
}): Promise<GisDatasetLink> {
  const { data } = await api.put<GisDatasetLink>('/gis-links/datasets', body)
  return data
}

export async function gisDeleteDatasetLink(gisDatasetId: string): Promise<void> {
  await api.delete(`/gis-links/datasets/${gisDatasetId}`)
}

// Правка редактируемых метаданных датасета GIS напрямую в dssgis (PATCH — частичное
// обновление). Проксируется через /gis/* с RBAC. Возвращает обновлённую карточку.
export async function gisUpdateDataset(
  id: string,
  body: { description?: string | null },
): Promise<GisDatasetDetail> {
  const { data } = await api.patch<GisDatasetDetail>(`/gis/datasets/${id}`, body)
  return data
}
