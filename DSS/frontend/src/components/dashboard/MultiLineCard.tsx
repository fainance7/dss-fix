'use client'

/**
 * Линейный график по одному или нескольким показателям областного уровня.
 *
 * Используется на дашбордах, где спецификация требует «линейный график за
 * 2014–2024 гг.» (индексы, доли, потребление) — в отличие от карт-показателей,
 * которые рисует IndicatorView. Каждая серия = один показатель; значение за год
 * усредняется по регионам (для областных показателей это и есть областное
 * значение).
 */

import { useEffect, useMemo, useState } from 'react'
import {
  fetchApprovedValues,
  type IndicatorValuesListItem,
} from '@/lib/api/backend'
import { LineChart, LINE_COLORS, WIDE_CHART } from './DirectionDashboard'

export interface LineSeriesSpec {
  /** id показателя в справочнике; null — показатель не найден (серия пустует). */
  id: string | null
  name: string
  color: string
}

interface MultiLineCardProps {
  title: string
  description?: string
  unit?: string
  series: LineSeriesSpec[]
  /** Ограничение оси X (включительно). По умолчанию — весь доступный диапазон. */
  yearMin?: number
  yearMax?: number
}

export default function MultiLineCard({
  title,
  description,
  unit,
  series,
  yearMin,
  yearMax,
}: MultiLineCardProps) {
  const [valuesById, setValuesById] = useState<
    Map<string, IndicatorValuesListItem[]>
  >(new Map())
  const [loading, setLoading] = useState(true)

  // Стабильный ключ для эффекта — список непустых id серий.
  const ids = useMemo(
    () => series.map((s) => s.id).filter((id): id is string => Boolean(id)),
    [series],
  )
  const idsKey = ids.join(',')

  useEffect(() => {
    let cancel = false
    setLoading(true)
    const loadOne = async (indicatorId: string) =>
      [indicatorId, await fetchApprovedValues({ indicator_id: indicatorId })] as const
    Promise.all(ids.map(loadOne))
      .then((pairs) => {
        if (cancel) return
        setValuesById(new Map(pairs))
      })
      .finally(() => {
        if (!cancel) setLoading(false)
      })
    return () => {
      cancel = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey])

  const chartData = useMemo(() => {
    const out: { x: string; y: number; group: string }[] = []
    for (const s of series) {
      if (!s.id) continue
      const ivs = valuesById.get(s.id) ?? []
      const byYear = new Map<string, number[]>()
      for (const v of ivs) {
        const yr = Number(v.period_start.slice(0, 4))
        if (yearMin !== undefined && yr < yearMin) continue
        if (yearMax !== undefined && yr > yearMax) continue
        const key = String(yr)
        const arr = byYear.get(key) ?? []
        arr.push(v.value)
        byYear.set(key, arr)
      }
      for (const [yr, arr] of byYear) {
        out.push({
          x: yr,
          y: arr.reduce((a, b) => a + b, 0) / arr.length,
          group: s.name,
        })
      }
    }
    return out
  }, [series, valuesById, yearMin, yearMax])

  return (
    <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5">
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <h3 className="text-base font-semibold text-gray-900">{title}</h3>
        {unit && <span className="text-sm text-gray-400">{unit}</span>}
      </div>
      {description && <p className="text-sm text-gray-500 mb-3">{description}</p>}
      {loading ? (
        <div className="flex h-40 items-center justify-center text-base text-gray-400">
          Загрузка…
        </div>
      ) : (
        <LineChart data={chartData} unit={unit} width={WIDE_CHART.width} height={WIDE_CHART.height} />
      )}
      {chartData.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-3 text-sm text-gray-600">
          {Array.from(new Set(chartData.map((p) => p.group))).map((g, gi) => (
            <span key={g} className="flex items-center gap-1.5">
              <span
                className="inline-block h-2 w-3 rounded-sm"
                style={{ background: LINE_COLORS[gi % LINE_COLORS.length] }}
              />
              {g}
            </span>
          ))}
        </div>
      )}
    </section>
  )
}
