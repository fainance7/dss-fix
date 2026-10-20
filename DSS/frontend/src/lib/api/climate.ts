import api from '@/lib/api'

// Помесячные климатические данные по метеостанциям (backend/app/api/v1/climate.py).
// Месяцы рассчитываются из декад: температура — среднее, взвешенное по дням,
// осадки — сумма трёх декад.

export type ClimateIndicatorKey = 'temperature' | 'precipitation'

export interface ClimateDekad {
  dekad: number
  value: number
  status: string
  /** «по станции …» — декада восполнена по соседней станции (пометка источника) */
  derivation?: string | null
}

export interface ClimateMonth {
  month: number
  value: number | null
  status: string | null
  n_stations: number
  norm: number | null
  norm_years: number
  anomaly: number | null
  dekads?: ClimateDekad[]
  complete?: boolean
  /** Станции, чьи декады этого месяца восполнены по соседней станции */
  substitutes?: string[]
}

export interface ClimateMonthly {
  indicator: { id: string; name: string; unit: string | null; key: ClimateIndicatorKey; aggregation: string }
  scope: {
    type: 'station' | 'region' | 'oblast'
    id: string | null
    name: string
    region?: string
    basis: 'station' | 'district_mean' | 'nearest_station' | 'stations_mean'
    stations: string[]
  }
  year: number | null
  years: number[]
  base_period: [number, number] | null
  months: ClimateMonth[]
}

export interface ClimateStationsRef {
  stations: { id: string; name: string; region_id: string; region: string; lat: number | null; lon: number | null }[]
  regions: { id: string; name: string; nearest_only: boolean; stations: string[] }[]
}

export async function fetchClimateMonthly(params: {
  indicator: ClimateIndicatorKey
  station_id?: string
  region_id?: string
  year?: number
}): Promise<ClimateMonthly> {
  const { data } = await api.get<ClimateMonthly>('/climate/monthly', { params })
  return data
}

export async function fetchClimateStations(): Promise<ClimateStationsRef> {
  const { data } = await api.get<ClimateStationsRef>('/climate/stations')
  return data
}

// Справочные многолетние таблицы заказчика (backend/app/data/climate_reference.json).
interface ClimateRefRow {
  region: string
  region_id: string | null
  stations: string[]
}

export interface ClimateTransitionRow extends ClimateRefRow {
  d5_spring: string
  d5_autumn: string
  n5: number
  d10_spring: string
  d10_autumn: string
  n10: number
  d15_spring: string
  d15_autumn: string
  n15: number
}

export interface ClimateActiveSumsRow extends ClimateRefRow {
  t5: number
  t10: number
  t15: number
}

export interface ClimateProbabilityRow extends ClimateRefRow {
  values: number[]
}

interface ClimateRefTable<R> {
  title: string
  rows: R[]
  description: string[]
}

export interface ClimateReference {
  source: string
  base_period: [number, number]
  transitions: ClimateRefTable<ClimateTransitionRow>
  active_sums: ClimateRefTable<ClimateActiveSumsRow>
  probability: ClimateRefTable<ClimateProbabilityRow> & { levels: number[] }
  k_classes_multiyear: { title: string; description: string[] }
}

export async function fetchClimateReference(): Promise<ClimateReference> {
  const { data } = await api.get<ClimateReference>('/climate/reference')
  return data
}
