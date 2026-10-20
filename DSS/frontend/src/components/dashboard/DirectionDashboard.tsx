'use client'

import { useEffect, useMemo, useState } from 'react'
import dynamic from 'next/dynamic'
import { clsx } from 'clsx'
import {
  fetchRegions,
  fetchIndicators,
  fetchApprovedValues,
  fetchDirections,
  type Region,
  type Indicator,
  type IndicatorValuesListItem,
} from '@/lib/api/backend'
import { isDateCodeUnit, summarizeDateCodes } from '@/lib/dateCode'
import { linearTrend } from '@/lib/climate/trend'
import { AXIS_FONT, YAxis, formatNumber, formatValue, niceScale, unitLabel, yAxisPad } from './chartKit'
import IndicatorView from './IndicatorView'
import PieChart, { type PieSegment } from './PieChart'
import TabSummary from './TabSummary'
import MonthlyClimateCard from './MonthlyClimateCard'
import type { ClimateIndicatorKey } from '@/lib/api/climate'
import { CLIMATE_COLOR_SCALES, type ColorBin } from '@/lib/climate/colorScales'
import { WATER_COLOR_SCALES } from '@/lib/water/colorScales'

// Карта из каталога геоданных: maplibre грузится только для вкладок с таким блоком.
const GeoLayersPanel = dynamic(() => import('./GeoLayersPanel'), {
  ssr: false,
  loading: () => <div className="h-[460px] animate-pulse rounded-xl bg-gray-100" />,
})

/**
 * Generic dashboard for a single development direction.
 *
 * Each tab is a list of "blocks" (cards / charts / tables) sourced from
 * `indicator_values`. The component handles loading, RBAC-friendly empty
 * states, and a simple inline SVG time-series chart for trend visualization.
 *
 * Wrapping pages (e.g. /dashboard/climate) only need to specify the
 * direction code and tab/block configuration — they don't fetch data.
 */

export type BlockType = 'kpi' | 'line' | 'table' | 'placeholder' | 'choropleth' | 'pie' | 'geo' | 'monthly'

export interface DashboardBlock {
  /** A short title shown above the block (above KPI cards or chart). */
  title: string
  /** Block visual type. */
  type: BlockType
  /**
   * Names of indicators (substring match against `Indicator.name`) to pull.
   * Empty/omitted = use any indicator from this direction.
   * For 'choropleth' the FIRST matched indicator drives the map.
   */
  indicators?: string[]
  /** For 'placeholder' blocks: explanatory description shown to user. */
  description?: string
  /** Hint to the user about what visualization is planned. */
  vizHint?: string
  /** For 'choropleth': key into climate/water color-scale registry (heatmap bins). */
  colorBinsKey?: string
  /** For 'choropleth': dynamics panel chart type passed to IndicatorView (default 'bar'). */
  dynamicsChart?: 'bar' | 'line'
  /** For 'pie': segments come from oblast-level rows (region.level === 1). */
  pieOblast?: boolean
  /** For 'pie': if segments are shares summing < 100, append a remainder segment with this label. */
  pieComplementLabel?: string
  /** For 'placeholder': concrete sentence about the missing data (overrides the generic subtext). */
  missingReason?: string
  /** For 'geo': ключи слоёв каталога геоданных (/dashboard-geo), порядок кнопок. */
  geoLayers?: string[]
  /** For 'geo': слои, включённые при открытии (по умолчанию — первый). */
  geoInitial?: string[]
  /** For 'monthly': помесячный ряд по метеостанциям (месяцы рассчитаны из декад). */
  climateIndicator?: ClimateIndicatorKey
}

export interface DashboardTab {
  id: string
  title: string
  description?: string
  blocks: DashboardBlock[]
}

export interface DirectionDashboardProps {
  /** Direction code matching `directions.code` in DB ("economy" | "climate" | …). */
  directionCode: string
  /** Human-readable title shown in page header. */
  directionLabel: string
  tabs: DashboardTab[]
}

// ── small utilities ─────────────────────────────────────────────────

/** Подстрока без учёта регистра; запрос с префиксом «=» — точное совпадение имени
 *  (нужно, когда имя показателя входит в имя другого, напр. «Площадь пашни»). */
function matchesIndicator(ind: Indicator, queries: string[] | undefined): boolean {
  if (!queries || queries.length === 0) return true
  const name = ind.name.toLowerCase()
  return queries.some((q) =>
    q.startsWith('=') ? name === q.slice(1).toLowerCase() : name.includes(q.toLowerCase()),
  )
}

/** Resolve a choropleth color-scale key to bins (climate registry first, then water). */
function resolveColorBins(key?: string): ColorBin[] | undefined {
  if (!key) return undefined
  const climate = (CLIMATE_COLOR_SCALES as Record<string, { bins: ColorBin[] }>)[key]
  if (climate) return climate.bins
  const water = (WATER_COLOR_SCALES as Record<string, { bins: ColorBin[] }>)[key]
  if (water) return water.bins
  return undefined
}

// ── inline SVG line chart ───────────────────────────────────────────

export interface LineChartProps {
  data: { x: string; y: number; group?: string }[]
  width?: number
  height?: number
  label?: string
  /** Единица измерения — подпись вертикальной шкалы. */
  unit?: string
  /** Если true — под каждой линией рисуется пунктирная линия линейного тренда. */
  showTrend?: boolean
}

/** Поле графика для карточек во всю ширину страницы (подписи не раздуваются). */
export const WIDE_CHART = { width: 1240, height: 380 }

export const LINE_COLORS = ['#2563eb', '#16a34a', '#dc2626', '#9333ea', '#ea580c', '#0891b2']

/**
 * Простой inline-SVG линейный график (год → значение, одна линия на группу)
 * с вертикальной шкалой и подписью единицы измерения.
 * Экспортируется для переиспользования на дашбордах (напр. тренды качества воды).
 */
export function LineChart({ data, width = 720, height = 300, label, unit, showTrend }: LineChartProps) {
  if (data.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center rounded-lg border border-dashed border-gray-300 text-lg text-gray-400">
        Нет данных
      </div>
    )
  }
  const groups = Array.from(new Set(data.map((p) => p.group ?? 'default')))
  const xs = Array.from(new Set(data.map((p) => p.x))).sort()
  const ys = data.map((p) => p.y)
  const scale = niceScale(Math.min(0, ...ys), Math.max(0, ...ys))
  const padL = yAxisPad(scale, true), padR = 84, padT = 22, padB = 36
  const innerW = width - padL - padR
  const innerH = height - padT - padB
  const xToPx = (x: string) =>
    padL + (xs.indexOf(x) / Math.max(1, xs.length - 1)) * innerW
  const yToPx = (y: number) =>
    padT + (1 - (y - scale.min) / (scale.max - scale.min)) * innerH

  // Прореживание подписей оси X: не больше ~12 меток, иначе годы сливаются.
  const xLabelStep = Math.max(1, Math.ceil(xs.length / 12))
  const lastIdx = xs.length - 1

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto">
      <YAxis
        scale={scale}
        x={padL}
        top={padT}
        height={innerH}
        gridTo={width - padR}
        unit={unitLabel(unit)}
      />
      {xs.map((x, i) => {
        if (i !== lastIdx && (i % xLabelStep !== 0 || lastIdx - i < xLabelStep)) return null
        return (
          <text
            key={i}
            x={xToPx(x)}
            y={height - padB + 20}
            textAnchor="middle"
            style={{ fontSize: AXIS_FONT, fill: '#6b7280' }}
          >
            {x.length > 4 ? x.slice(0, 4) : x}
          </text>
        )
      })}
      {/* lines per group */}
      {groups.map((g, gi) => {
        const color = LINE_COLORS[gi % LINE_COLORS.length]
        const points = data
          .filter((p) => (p.group ?? 'default') === g)
          .sort((a, b) => a.x.localeCompare(b.x))
        const path = points
          .map((p, i) => `${i === 0 ? 'M' : 'L'} ${xToPx(p.x)} ${yToPx(p.y)}`)
          .join(' ')
        const last = points[points.length - 1]
        const trend = showTrend ? linearTrend(points.map((p) => p.y)) : null
        return (
          <g key={g}>
            <path d={path} fill="none" stroke={color} strokeWidth={2} />
            {trend && points.length > 1 && (
              <line
                x1={xToPx(points[0].x)}
                x2={xToPx(points[points.length - 1].x)}
                y1={yToPx(trend.yStart)}
                y2={yToPx(trend.yEnd)}
                stroke={color}
                strokeWidth={1.5}
                strokeDasharray="5,4"
                opacity={0.6}
              >
                <title>{`Тренд ${g}: ${formatValue(trend.yStart)} → ${formatValue(trend.yEnd)}`}</title>
              </line>
            )}
            {points.map((p, i) => (
              <circle key={i} cx={xToPx(p.x)} cy={yToPx(p.y)} r={3.5} fill={color}>
                {/* Нативный hover-tooltip — без JS, работает в любом браузере */}
                <title>{`${g === 'default' ? '' : g + '\n'}${p.x}: ${formatValue(p.y)}${unit ? ' ' + unit : ''}`}</title>
              </circle>
            ))}
            {last && (
              <text
                x={xToPx(last.x) + 7}
                y={yToPx(last.y) + 4}
                className="fill-gray-700"
                style={{ fontSize: AXIS_FONT, fontWeight: 600 }}
              >
                {formatValue(last.y)}
              </text>
            )}
          </g>
        )
      })}
      {label && (
        <text x={width - padR} y={padT - 8} textAnchor="end" style={{ fontSize: AXIS_FONT, fill: '#6b7280' }}>
          {label}
        </text>
      )}
    </svg>
  )
}

// ── block renderers ──────────────────────────────────────────────────

function KpiBlock({
  block,
  values,
  indicators,
}: {
  block: DashboardBlock
  values: IndicatorValuesListItem[]
  indicators: Indicator[]
}) {
  // For each matching indicator, take the latest value across regions.
  const matched = indicators.filter((i) => matchesIndicator(i, block.indicators))
  if (matched.length === 0) {
    return <EmptyBlock title={block.title} reason="Нет показателей в этой выборке" />
  }

  const cards = matched.slice(0, 6).map((ind) => {
    const ivs = values.filter((v) => v.indicator_id === ind.id)
    // Берём среднее по регионам в каждом году — KPI отражает областной срез,
    // иначе один регион «перетягивает» карточку.
    const byYear = new Map<string, number[]>()
    for (const v of ivs) {
      const yr = v.period_start.slice(0, 4)
      const arr = byYear.get(yr) ?? []
      arr.push(v.value)
      byYear.set(yr, arr)
    }
    const years = Array.from(byYear.keys()).sort()
    const last = years[years.length - 1]
    const prev = years[years.length - 2]
    const mean = (arr: number[]) => arr.reduce((a, b) => a + b, 0) / arr.length

    // Показатели-даты (формат ДД.ММ) нельзя усреднять как числа: вместо среднего
    // показываем типичную (медианную) дату и диапазон по районам за последний год.
    const isDate = isDateCodeUnit(ind.unit_name)
    const dateSummary = isDate && last ? summarizeDateCodes(byYear.get(last)!) : null

    const lastVal = last ? mean(byYear.get(last)!) : null
    const prevVal = prev ? mean(byYear.get(prev)!) : null
    const delta =
      !isDate && lastVal !== null && prevVal !== null && prevVal !== 0
        ? ((lastVal - prevVal) / Math.abs(prevVal)) * 100
        : null
    return {
      key: ind.id,
      label: ind.name,
      unit: isDate ? undefined : ind.unit_name,
      value: lastVal,
      dateText: dateSummary?.typical ?? (isDate ? '—' : null),
      rangeText: dateSummary?.range ?? null,
      period: last ?? null,
      prevPeriod: prev ?? null,
      delta,
      regions: ivs.length > 0 ? new Set(ivs.map((v) => v.region_id)).size : 0,
    }
  })

  return (
    <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5">
      <h3 className="text-base font-semibold text-gray-900 mb-3">{block.title}</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {cards.map((c) => {
          const dColor =
            c.delta === null
              ? 'text-gray-400'
              : c.delta > 0
                ? 'text-green-700'
                : c.delta < 0
                  ? 'text-red-700'
                  : 'text-gray-500'
          const dSign =
            c.delta === null ? '' : c.delta > 0 ? '↑' : c.delta < 0 ? '↓' : '→'
          return (
            <div key={c.key} className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3">
              <div className="text-sm text-gray-500 line-clamp-2 min-h-[32px]">
                {c.label}
              </div>
              <div className="mt-1 flex items-baseline gap-1.5">
                <div
                  className={clsx(
                    'font-semibold text-gray-900',
                    c.dateText !== null ? 'text-lg' : 'text-2xl',
                  )}
                >
                  {c.dateText !== null
                    ? c.dateText
                    : c.value !== null
                      ? formatValue(c.value)
                      : '—'}
                </div>
                {c.unit && (
                  <span className="text-sm text-gray-500">{c.unit}</span>
                )}
                {c.delta !== null && (
                  <span className={`ml-auto text-sm font-medium ${dColor}`}>
                    {dSign} {formatNumber(Math.abs(c.delta), 1)} %
                  </span>
                )}
              </div>
              <div className="text-xs text-gray-400 mt-0.5 flex justify-between gap-2">
                <span>
                  {c.rangeText ? `диапазон ${c.rangeText} · ` : ''}
                  {c.period
                    ? c.prevPeriod && c.dateText === null
                      ? `${c.prevPeriod} → ${c.period}`
                      : `за ${c.period}`
                    : 'нет загруженных значений'}
                </span>
                {c.regions > 0 && (
                  <span title="Регионов с данными">{c.regions} р-нов</span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function LineBlock({
  block,
  values,
  indicators,
  regions,
}: {
  block: DashboardBlock
  values: IndicatorValuesListItem[]
  indicators: Indicator[]
  regions: Region[]
}) {
  const matched = indicators.filter((i) => matchesIndicator(i, block.indicators))
  // Build chart data: x = year, y = value, group = indicator (one line per indicator)
  const ivByInd = new Map<string, IndicatorValuesListItem[]>()
  for (const v of values) {
    if (!matched.some((i) => i.id === v.indicator_id)) continue
    const arr = ivByInd.get(v.indicator_id) ?? []
    arr.push(v)
    ivByInd.set(v.indicator_id, arr)
  }
  const chartData: { x: string; y: number; group?: string }[] = []
  const shown = matched.slice(0, 6)
  const legendName = (ind: Indicator) =>
    ind.unit_name ? `${ind.name}, ${ind.unit_name}` : ind.name
  for (const ind of shown) {
    const ivs = ivByInd.get(ind.id) ?? []
    // Aggregate by year (mean across regions to keep chart clean).
    const byYear = new Map<string, number[]>()
    for (const v of ivs) {
      const yr = v.period_start.slice(0, 4)
      const arr = byYear.get(yr) ?? []
      arr.push(v.value)
      byYear.set(yr, arr)
    }
    for (const [yr, arr] of byYear) {
      const mean = arr.reduce((a, b) => a + b, 0) / arr.length
      chartData.push({ x: yr, y: mean, group: legendName(ind) })
    }
  }
  // Единица шкалы: общая для всех линий, иначе — перечень (единица каждой
  // линии указана в легенде).
  const units = Array.from(
    new Set(shown.filter((i) => ivByInd.has(i.id)).map((i) => unitLabel(i.unit_name))),
  )

  return (
    <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5">
      <h3 className="text-base font-semibold text-gray-900 mb-3">{block.title}</h3>
      {regions.length > 0 && (
        <div className="text-sm text-gray-400 mb-2">
          Среднее по {regions.length} регионам
        </div>
      )}
      <LineChart
        data={chartData}
        width={WIDE_CHART.width}
        height={WIDE_CHART.height}
        showTrend
        unit={units.join(' / ')}
      />
      {chartData.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-3 text-sm text-gray-600">
          {Array.from(new Set(chartData.map((p) => p.group ?? 'default'))).map(
            (g, gi) => (
              <span key={g} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-2 w-3 rounded-sm"
                  style={{ background: LINE_COLORS[gi % LINE_COLORS.length] }}
                />
                {g}
              </span>
            ),
          )}
        </div>
      )}
    </section>
  )
}

function TableBlock({
  block,
  values,
  indicators,
  regions,
}: {
  block: DashboardBlock
  values: IndicatorValuesListItem[]
  indicators: Indicator[]
  regions: Region[]
}) {
  const matched = indicators.filter((i) => matchesIndicator(i, block.indicators))
  // matrix: rows=region, cols=indicator, value=latest
  const rows = regions
    .filter((r) => values.some((v) => v.region_id === r.id))
    .sort((a, b) => a.name.localeCompare(b.name))

  return (
    <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5">
      <h3 className="text-base font-semibold text-gray-900 mb-3">{block.title}</h3>
      {rows.length === 0 || matched.length === 0 ? (
        <EmptyBlock title="" reason="Нет загруженных значений" inline />
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-base">
            <thead>
              <tr className="text-left text-sm text-gray-500 border-b border-gray-200">
                <th className="px-2 py-2 sticky left-0 bg-white">Район</th>
                {matched.slice(0, 5).map((i) => (
                  <th key={i.id} className="px-2 py-2 whitespace-nowrap">
                    {i.name.slice(0, 30)}
                    {i.name.length > 30 ? '…' : ''}
                    {i.unit_name ? `, ${i.unit_name}` : ''}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-gray-100">
                  <td className="px-2 py-2 font-medium text-gray-700 sticky left-0 bg-white">
                    {r.name}
                  </td>
                  {matched.slice(0, 5).map((i) => {
                    const v = values
                      .filter((x) => x.region_id === r.id && x.indicator_id === i.id)
                      .sort((a, b) => b.period_start.localeCompare(a.period_start))[0]
                    return (
                      <td key={i.id} className="px-2 py-2 tabular-nums text-gray-700">
                        {v ? formatValue(v.value) : '—'}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

const PIE_PALETTE = ['#2563eb', '#16a34a', '#dc2626', '#9333ea', '#ea580c', '#0891b2']

/** Choropleth block — reuses the self-contained IndicatorView (map + dynamics + ranking). */
function ChoroplethBlock({
  block,
  values,
  indicators,
}: {
  block: DashboardBlock
  values: IndicatorValuesListItem[]
  indicators: Indicator[]
}) {
  // Substring-match может дать несколько кандидатов (напр. «Общая площадь земель»
  // ⊂ «…водного фонда»). Берём кандидата с наибольшим числом approved-значений —
  // это устойчиво выбирает целевой показатель с реальными данными.
  const candidates = indicators.filter((i) => matchesIndicator(i, block.indicators))
  const ind = candidates
    .map((i) => ({ i, n: values.reduce((c, v) => c + (v.indicator_id === i.id ? 1 : 0), 0) }))
    .sort((a, b) => b.n - a.n)[0]?.i
  if (!ind) {
    return (
      <PlaceholderBlock
        block={{
          ...block,
          type: 'placeholder',
          missingReason:
            block.missingReason ??
            `Показатель «${block.indicators?.[0]?.replace(/^=/, '') ?? '—'}» не найден в справочнике направления.`,
        }}
      />
    )
  }
  const hasValues = values.some((v) => v.indicator_id === ind.id)
  if (!hasValues) {
    return (
      <PlaceholderBlock
        block={{
          ...block,
          type: 'placeholder',
          missingReason:
            block.missingReason ??
            `Нет одобренных значений показателя «${ind.name}» для карты районов.`,
        }}
      />
    )
  }
  return (
    <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5">
      <h3 className="text-base font-semibold text-gray-900 mb-3">{block.title}</h3>
      <IndicatorView
        indicatorId={ind.id}
        indicatorName={ind.name}
        unit={ind.unit_name ?? undefined}
        colorBins={resolveColorBins(block.colorBinsKey)}
        dynamicsChart={block.dynamicsChart}
      />
    </section>
  )
}

/** Pie block — structure of several related indicators at their latest common year. */
function PieBlock({
  block,
  values,
  indicators,
  regions,
}: {
  block: DashboardBlock
  values: IndicatorValuesListItem[]
  indicators: Indicator[]
  regions: Region[]
}) {
  // Resolve each requested name (in order) to a single indicator.
  const matched: Indicator[] = []
  for (const q of block.indicators ?? []) {
    const ind = indicators.find((i) => matchesIndicator(i, [q]))
    if (ind && !matched.some((m) => m.id === ind.id)) matched.push(ind)
  }

  const oblastIds = new Set(regions.filter((r) => r.level === 1).map((r) => r.id))
  const inScope = (v: IndicatorValuesListItem) =>
    block.pieOblast ? oblastIds.has(v.region_id) : true

  // Latest year that has data for at least one matched indicator (within scope).
  const yearsByInd = new Map<string, Map<string, number[]>>()
  let latestYear = ''
  for (const ind of matched) {
    const byYear = new Map<string, number[]>()
    for (const v of values) {
      if (v.indicator_id !== ind.id || !inScope(v)) continue
      const yr = v.period_start.slice(0, 4)
      const arr = byYear.get(yr) ?? []
      arr.push(v.value)
      byYear.set(yr, arr)
      if (yr > latestYear) latestYear = yr
    }
    yearsByInd.set(ind.id, byYear)
  }

  const segments: PieSegment[] = []
  matched.forEach((ind, i) => {
    const byYear = yearsByInd.get(ind.id)
    // Prefer the latest common year; fall back to the indicator's own latest.
    const arr =
      byYear?.get(latestYear) ??
      (byYear && byYear.size > 0
        ? byYear.get(Array.from(byYear.keys()).sort().at(-1) as string)
        : undefined)
    if (!arr || arr.length === 0) return
    const mean = arr.reduce((a, b) => a + b, 0) / arr.length
    if (!Number.isFinite(mean) || mean <= 0) return
    segments.push({ label: ind.name, value: mean, color: PIE_PALETTE[i % PIE_PALETTE.length] })
  })

  // Complement segment for share-based pies (values are % summing < 100).
  if (block.pieComplementLabel && segments.length > 0) {
    const sum = segments.reduce((s, seg) => s + seg.value, 0)
    if (sum > 0 && sum < 100) {
      segments.push({ label: block.pieComplementLabel, value: 100 - sum, color: '#cbd5e1' })
    }
  }

  if (segments.length === 0) {
    return (
      <PlaceholderBlock
        block={{
          ...block,
          type: 'placeholder',
          missingReason:
            block.missingReason ?? 'Нет одобренных значений для построения структуры.',
        }}
      />
    )
  }

  const unit = matched[0]?.unit_name ?? undefined
  return (
    <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5">
      <h3 className="text-base font-semibold text-gray-900 mb-3">{block.title}</h3>
      <PieChart segments={segments} unit={unit} year={latestYear || undefined} />
    </section>
  )
}

function PlaceholderBlock({ block }: { block: DashboardBlock }) {
  return (
    <section className="rounded-xl bg-gradient-to-br from-blue-50 to-purple-50 ring-1 ring-blue-100 p-5">
      <h3 className="text-base font-semibold text-gray-900 mb-1">{block.title}</h3>
      {block.description && (
        <p className="text-sm text-gray-600 mb-3">{block.description}</p>
      )}
      <div className="rounded-lg bg-white/70 border border-blue-200 px-4 py-8 text-center">
        <div className="text-base text-blue-700 font-medium">
          {block.vizHint ?? 'Визуализация будет добавлена'}
        </div>
        <div className="text-sm text-gray-500 mt-1">
          {block.missingReason ?? 'Требует загрузки специальных данных в БД (геометрия / детальные срезы)'}
        </div>
      </div>
    </section>
  )
}

function EmptyBlock({
  title,
  reason,
  inline = false,
}: {
  title: string
  reason: string
  inline?: boolean
}) {
  return (
    <section
      className={clsx(
        !inline && 'rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5',
      )}
    >
      {!inline && title && (
        <h3 className="text-base font-semibold text-gray-900 mb-2">{title}</h3>
      )}
      <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center text-base text-gray-500">
        {reason}
      </div>
    </section>
  )
}

// ── main component ──────────────────────────────────────────────────

export default function DirectionDashboard({
  directionCode,
  directionLabel,
  tabs,
}: DirectionDashboardProps) {
  const [tabId, setTabId] = useState(tabs[0]?.id ?? '')
  const [regions, setRegions] = useState<Region[]>([])
  const [directionId, setDirectionId] = useState<string | null>(null)
  const [indicators, setIndicators] = useState<Indicator[]>([])
  const [values, setValues] = useState<IndicatorValuesListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 1) Resolve directionCode → direction_id, then load regions and indicators.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([fetchRegions(), fetchDirections()])
      .then(async ([regs, dirs]) => {
        if (cancelled) return
        setRegions(regs)
        const dir = dirs.find((d) => d.code === directionCode)
        if (!dir) {
          setIndicators([])
          setDirectionId(null)
          return
        }
        setDirectionId(dir.id)
        const inds = await fetchIndicators({
          is_active: true,
          direction_id: dir.id,
        })
        if (!cancelled) setIndicators(inds)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        const msg = (e as { message?: string })?.message ?? 'Ошибка загрузки'
        setError(msg)
      })
    return () => {
      cancelled = true
    }
  }, [directionCode])

  // 2) Once we know the direction, load ALL its approved values (paginated —
  //    backend caps page_size at 1000, climate alone has 15K+ rows).
  useEffect(() => {
    let cancelled = false
    if (!directionId) {
      setValues([])
      setLoading(false)
      return
    }
    // Cap at 50K rows = 50 pages to avoid runaway loops.
    fetchApprovedValues({ direction_id: directionId, maxPages: 50 })
      .then((items) => {
        if (cancelled) return
        // Блоки дашборда годовые: подгодовые ряды (месячные, декадные) показывает
        // только блок 'monthly' — иначе месяцы усреднялись бы в «год».
        setValues(items.filter((v) => v.period_type === 'year'))
      })
      .catch((e: unknown) => {
        if (cancelled) return
        const msg = (e as { message?: string })?.message ?? 'Ошибка загрузки данных'
        setError(msg)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [directionId])

  const currentTab = tabs.find((t) => t.id === tabId) ?? tabs[0]

  // Показатели вкладки с данными — по ним строится краткий вывод ИИ.
  const tabIndicatorIds = useMemo(() => {
    if (loading || !currentTab) return []
    const count = new Map<string, number>()
    for (const v of values) count.set(v.indicator_id, (count.get(v.indicator_id) ?? 0) + 1)
    const ids: string[] = []
    for (const b of currentTab.blocks) {
      if (b.type === 'placeholder' || !b.indicators?.length) continue
      const matched = indicators.filter((i) => count.has(i.id) && matchesIndicator(i, b.indicators))
      // Картограмма показывает одного кандидата — с наибольшим числом значений.
      const shown =
        b.type === 'choropleth'
          ? matched.sort((a, c) => count.get(c.id)! - count.get(a.id)!).slice(0, 1)
          : matched.slice(0, 6)
      ids.push(...shown.map((i) => i.id))
    }
    return ids
  }, [loading, currentTab, values, indicators])

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex items-baseline justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">{directionLabel}</h1>
          <p className="text-base text-gray-500 mt-1">
            Дашборд направления развития
          </p>
        </div>
        {loading ? (
          <span className="text-sm text-gray-400">Загрузка…</span>
        ) : (
          <span className="text-sm text-gray-400">
            {indicators.length} показателей · {values.length} значений
          </span>
        )}
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-base text-red-700">
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <div className="flex gap-1 -mb-px overflow-x-auto">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTabId(t.id)}
              className={clsx(
                'px-4 py-2.5 text-base font-medium border-b-2 whitespace-nowrap transition-colors',
                tabId === t.id
                  ? 'border-primary-600 text-primary-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
              )}
            >
              {t.title}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      {currentTab && (
        <div className="space-y-4">
          {currentTab.description && (
            <p className="text-base text-gray-600">{currentTab.description}</p>
          )}
          <TabSummary
            tabId={currentTab.id}
            title={`${directionLabel}: ${currentTab.title}`}
            directionCode={directionCode}
            indicatorIds={tabIndicatorIds}
          />
          {currentTab.blocks.map((b, idx) => {
            const key = `${currentTab.id}-${idx}`
            switch (b.type) {
              case 'kpi':
                return (
                  <KpiBlock
                    key={key}
                    block={b}
                    values={values}
                    indicators={indicators}
                  />
                )
              case 'line':
                return (
                  <LineBlock
                    key={key}
                    block={b}
                    values={values}
                    indicators={indicators}
                    regions={regions}
                  />
                )
              case 'table':
                return (
                  <TableBlock
                    key={key}
                    block={b}
                    values={values}
                    indicators={indicators}
                    regions={regions}
                  />
                )
              case 'choropleth':
                return (
                  <ChoroplethBlock
                    key={key}
                    block={b}
                    values={values}
                    indicators={indicators}
                  />
                )
              case 'pie':
                return (
                  <PieBlock
                    key={key}
                    block={b}
                    values={values}
                    indicators={indicators}
                    regions={regions}
                  />
                )
              case 'geo':
                return (
                  <GeoLayersPanel
                    key={key}
                    title={b.title}
                    description={b.description}
                    layers={b.geoLayers ?? []}
                    initial={b.geoInitial}
                  />
                )
              case 'monthly':
                return (
                  <MonthlyClimateCard
                    key={key}
                    title={b.title}
                    description={b.description}
                    indicator={b.climateIndicator ?? 'temperature'}
                  />
                )
              case 'placeholder':
              default:
                return <PlaceholderBlock key={key} block={b} />
            }
          })}
        </div>
      )}
    </div>
  )
}
