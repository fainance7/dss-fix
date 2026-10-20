'use client'

// KPI-карточки для климатических дашбордов. Считает агрегаты по районам за
// последний доступный год для каждого показателя: среднее, мин/макс, район с
// мин/макс. Данные берёт сам (fetchRegions + fetchIndicatorValues), показателей
// в дашборде немного, поэтому отдельный лёгкий фетч оправдан.

import { useEffect, useMemo, useState } from 'react'
import {
  fetchRegions,
  fetchApprovedValues,
  type IndicatorValuesListItem,
} from '@/lib/api/backend'
import { formatNumber } from './chartKit'

export type KpiAgg = 'avg' | 'min' | 'max' | 'argmin' | 'argmax'

export interface ClimateKpiMetric {
  label: string
  /** id показателя (резолвится страницей); null — карточка покажет «—». */
  indicatorId: string | null
  agg: KpiAgg
  unit?: string
  /** Кол-во знаков после запятой для числовых агрегатов. По умолчанию 1. */
  decimals?: number
}

interface ComputedKpi {
  label: string
  /** Число (avg/min/max) или название района (argmin/argmax) или null. */
  value: number | string | null
  unit?: string
  /** Год, за который посчитано. */
  year: string | null
}

const loadValues = (indicatorId: string): Promise<IndicatorValuesListItem[]> =>
  fetchApprovedValues({ indicator_id: indicatorId, period_type: 'year' })

export default function ClimateKpiStrip({
  title,
  metrics,
}: {
  title?: string
  metrics: ClimateKpiMetric[]
}) {
  const [regionName, setRegionName] = useState<Map<string, string>>(new Map())
  const [valuesById, setValuesById] = useState<Map<string, IndicatorValuesListItem[]>>(new Map())
  const [loading, setLoading] = useState(true)

  const ids = useMemo(
    () => Array.from(new Set(metrics.map((m) => m.indicatorId).filter((x): x is string => Boolean(x)))),
    [metrics],
  )
  const idsKey = ids.join(',')

  useEffect(() => {
    let cancel = false
    setLoading(true)
    Promise.all([fetchRegions(), Promise.all(ids.map(loadValues))])
      .then(([regs, perId]) => {
        if (cancel) return
        setRegionName(new Map(regs.map((r) => [r.id, r.name])))
        setValuesById(new Map(ids.map((id, i) => [id, perId[i]])))
      })
      .finally(() => {
        if (!cancel) setLoading(false)
      })
    return () => {
      cancel = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey])

  const computed: ComputedKpi[] = useMemo(() => {
    return metrics.map((m) => {
      if (!m.indicatorId) return { label: m.label, value: null, unit: m.unit, year: null }
      const ivs = valuesById.get(m.indicatorId) ?? []
      if (ivs.length === 0) return { label: m.label, value: null, unit: m.unit, year: null }
      // Последний доступный год.
      const latest = ivs.reduce((a, b) => (a.period_start > b.period_start ? a : b)).period_start
      const year = latest.slice(0, 4)
      // Один значение на район за последний год.
      const byRegion = new Map<string, number>()
      for (const v of ivs) {
        if (v.period_start.slice(0, 4) !== year) continue
        byRegion.set(v.region_id, v.value)
      }
      const entries = Array.from(byRegion.entries())
      if (entries.length === 0) return { label: m.label, value: null, unit: m.unit, year: null }

      let value: number | string
      if (m.agg === 'avg') {
        value = entries.reduce((s, [, v]) => s + v, 0) / entries.length
      } else if (m.agg === 'min') {
        value = Math.min(...entries.map(([, v]) => v))
      } else if (m.agg === 'max') {
        value = Math.max(...entries.map(([, v]) => v))
      } else {
        // argmin / argmax → название района
        const pick = entries.reduce((best, cur) =>
          m.agg === 'argmin'
            ? cur[1] < best[1]
              ? cur
              : best
            : cur[1] > best[1]
              ? cur
              : best,
        )
        value = regionName.get(pick[0]) ?? '—'
      }
      return { label: m.label, value, unit: m.unit, year }
    })
  }, [metrics, valuesById, regionName])

  return (
    <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5">
      {title && <h3 className="text-base font-semibold text-gray-900 mb-3">{title}</h3>}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
        {computed.map((c, i) => {
          const isNum = typeof c.value === 'number'
          const dec = metrics[i]?.decimals ?? 1
          return (
            <div key={i} className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3">
              <div className="text-sm text-gray-500 line-clamp-2 min-h-[32px]">{c.label}</div>
              <div className="mt-1 flex items-baseline gap-1.5">
                <div className="text-2xl font-semibold text-gray-900 leading-tight">
                  {loading
                    ? '…'
                    : c.value === null
                      ? '—'
                      : isNum
                        ? formatNumber(c.value as number, dec)
                        : (c.value as string)}
                </div>
                {isNum && c.unit && <span className="text-sm text-gray-500">{c.unit}</span>}
              </div>
              <div className="text-xs text-gray-400 mt-0.5">
                {c.year ? `за ${c.year} год` : !loading && c.value === null ? 'нет данных' : ''}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
