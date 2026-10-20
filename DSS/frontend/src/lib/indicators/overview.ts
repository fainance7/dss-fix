// Сводные показатели СКО для обзорных экранов (Дашборд, Аналитика, Карта направлений).
// Только реальные одобренные значения indicator_values: область — строка уровня 1,
// районы — уровня 2. Нет данных — нет числа (экран показывает «нет данных»).
'use client'

import { useEffect, useState } from 'react'
import {
  fetchDirections,
  fetchIndicators,
  DATA_YEAR,
  fetchApprovedValues,
  fetchRegions,
  type Indicator,
  type IndicatorValuesListItem,
  type Region,
} from '@/lib/api/backend'

export type YearSeries = Map<number, number>

export interface OverviewSpec {
  key: string
  direction: string // код направления
  candidates: string[] // имена по приоритету
  exact?: boolean // точное совпадение имени (у зарплаты десятки отраслевых «— …» рядов)
  additive?: boolean // область = сумма районов, если строки области нет
  higherIsBetter?: boolean
  normalize?: (v: number) => number
  /**
   * Ряд «на начало года» (кандидаты имён): значение, датированное годом Y, — это
   * состояние на 1 января Y, то есть на конец года Y−1. Дополняет основной ряд
   * (на конец года) там, где у него нет данных: так численность на 01.01.2026
   * становится значением отчётного 2025 года.
   */
  startOfYear?: string[]
}

export interface OverviewIndicator {
  spec: OverviewSpec
  ind: Indicator | null
  oblast: YearSeries
  districts: Map<string, YearSeries> // region_id → год → значение
}

// Одобренные годовые значения показателя целиком (не позже отчётного года).
export async function fetchAllIndicatorValues(indicatorId: string): Promise<IndicatorValuesListItem[]> {
  return fetchApprovedValues({ indicator_id: indicatorId, period_type: 'year' })
}

// Ряд «на начало года» → годы «на конец года» (Y → Y−1), не позже отчётного года.
async function fetchStartOfYearValues(indicatorId: string): Promise<IndicatorValuesListItem[]> {
  const raw = await fetchApprovedValues({ indicator_id: indicatorId, period_type: 'year', cap: false })
  const shift = (d: string) => `${Number(d.slice(0, 4)) - 1}${d.slice(4)}`
  return raw
    .map((v) => ({ ...v, period_start: shift(v.period_start), period_end: shift(v.period_end) }))
    .filter((v) => Number(v.period_start.slice(0, 4)) <= DATA_YEAR)
}

function fillMissing(target: YearSeries, extra: YearSeries): void {
  extra.forEach((v, y) => {
    if (!target.has(y)) target.set(y, v)
  })
}

export function resolveIndicator(indicators: Indicator[], spec: Pick<OverviewSpec, 'candidates' | 'exact'>): Indicator | null {
  for (const c of spec.candidates) {
    const needle = c.toLowerCase()
    const hit = indicators.find((i) =>
      spec.exact ? i.name.toLowerCase() === needle : i.name.toLowerCase().includes(needle),
    )
    if (hit) return hit
  }
  return null
}

// Часть рядов численности загружена в тысячах, часть — в людях. Самый малый район
// СКО — больше 10 тыс. жителей, поэтому значение < 5000 — это тысячи.
export function normalizePopulation(v: number): number {
  return Math.abs(v) < 5000 ? v * 1000 : v
}

export function splitByLevel(
  values: IndicatorValuesListItem[],
  regions: Region[],
  spec: OverviewSpec,
): { oblast: YearSeries; districts: Map<string, YearSeries> } {
  const level = new Map(regions.map((r) => [r.id, r.level]))
  const norm = spec.normalize ?? ((v: number) => v)
  const oblast: YearSeries = new Map()
  const districts = new Map<string, YearSeries>()
  for (const v of values) {
    if (v.period_type !== 'year') continue
    const year = Number(v.period_start.slice(0, 4))
    const value = norm(Number(v.value))
    if (!Number.isFinite(value)) continue
    const lvl = level.get(v.region_id)
    if (lvl === 1) oblast.set(year, value)
    else if (lvl === 2) {
      if (!districts.has(v.region_id)) districts.set(v.region_id, new Map())
      districts.get(v.region_id)!.set(year, value)
    }
  }
  // Сумма районов — только для аддитивных показателей и только когда есть все районы.
  if (spec.additive) {
    const total = regions.filter((r) => r.level === 2).length
    const years = new Set<number>()
    districts.forEach((s) => s.forEach((_, y) => years.add(y)))
    years.forEach((y) => {
      if (oblast.has(y)) return
      const vals = Array.from(districts.values()).map((s) => s.get(y)).filter((x): x is number => x !== undefined)
      if (total > 0 && vals.length === total) oblast.set(y, vals.reduce((a, b) => a + b, 0))
    })
  }
  return { oblast, districts }
}

export function latestYear(series: YearSeries, maxYear?: number): number | null {
  let best: number | null = null
  series.forEach((_, y) => {
    if ((maxYear === undefined || y <= maxYear) && (best === null || y > best)) best = y
  })
  return best
}

export function yoy(series: YearSeries, year: number): number | null {
  const cur = series.get(year)
  const prev = series.get(year - 1)
  if (cur === undefined || prev === undefined || prev === 0) return null
  return ((cur - prev) / Math.abs(prev)) * 100
}

export function sortedYears(series: YearSeries): number[] {
  return Array.from(series.keys()).sort((a, b) => a - b)
}

// Загрузка набора показателей: справочник направлений → показатели → значения.
export function useOverviewIndicators(specs: OverviewSpec[]) {
  const [data, setData] = useState<Record<string, OverviewIndicator>>({})
  const [regions, setRegions] = useState<Region[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const specKey = specs.map((s) => s.key).join('|')

  useEffect(() => {
    let cancel = false
    setLoading(true)
    setError(null)
    ;(async () => {
      const [dirs, regs] = await Promise.all([fetchDirections(), fetchRegions()])
      const codes = Array.from(new Set(specs.map((s) => s.direction)))
      const byCode = new Map<string, Indicator[]>()
      await Promise.all(
        codes.map(async (code) => {
          const dir = dirs.find((d) => d.code === code)
          byCode.set(code, dir ? await fetchIndicators({ direction_id: dir.id }) : [])
        }),
      )
      const entries = await Promise.all(
        specs.map(async (spec): Promise<[string, OverviewIndicator]> => {
          const ind = resolveIndicator(byCode.get(spec.direction) ?? [], spec)
          if (!ind) return [spec.key, { spec, ind: null, oblast: new Map(), districts: new Map() }]
          const values = await fetchAllIndicatorValues(ind.id)
          const split = splitByLevel(values, regs, spec)
          const soy = spec.startOfYear
            ? resolveIndicator(byCode.get(spec.direction) ?? [], { candidates: spec.startOfYear, exact: spec.exact })
            : null
          if (soy) {
            const extra = splitByLevel(await fetchStartOfYearValues(soy.id), regs, spec)
            fillMissing(split.oblast, extra.oblast)
            extra.districts.forEach((s, rid) => {
              if (!split.districts.has(rid)) split.districts.set(rid, new Map())
              fillMissing(split.districts.get(rid)!, s)
            })
          }
          return [spec.key, { spec, ind, ...split }]
        }),
      )
      if (cancel) return
      setRegions(regs)
      setData(Object.fromEntries(entries))
    })()
      .catch((e: unknown) => {
        if (!cancel) setError((e as { message?: string })?.message ?? 'Ошибка загрузки')
      })
      .finally(() => {
        if (!cancel) setLoading(false)
      })
    return () => {
      cancel = true
    }
    // specs — константы страниц; перезагрузка только при смене набора ключей
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [specKey])

  return { data, regions, loading, error }
}

export function formatNumber(v: number, digits = 1): string {
  return v.toLocaleString('ru-RU', { maximumFractionDigits: digits })
}
