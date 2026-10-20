'use client'

/**
 * Диаграммы панели показателя (IndicatorView): динамика по годам (столбцы,
 * линия, несколько линий, накопление, столбцы + линия) и сравнение районов
 * (линейчатая, линейчатая с накоплением). У каждой диаграммы — шкала значений
 * с делениями и подписью единицы измерения.
 */

import { useMemo, type ReactNode } from 'react'
import { linearTrend } from '@/lib/climate/trend'
import {
  AXIS_FONT,
  XValueAxis,
  YAxis,
  formatValue,
  niceScale,
  valueFormatter,
  unitLabel,
  yAxisPad,
  type Scale,
} from './chartKit'

// Цвета оверлеев на «Динамике по годам».
const TREND_COLOR = '#dc2626' // тренд — красный пунктир
const NORM_COLOR = '#15803d' // среднемноголетняя норма — зелёный пунктир
const DIVERGING_NEG = '#f97316' // отрицательное отклонение (дефицит) — оранжевый
const DIVERGING_POS = '#2563eb' // положительное отклонение (профицит) — синий

// Геометрия диаграмм динамики (единицы viewBox).
const W = 720
const H = 300
const PAD_R = 16
const PAD_T = 16
const PAD_B = 36

// Геометрия линейчатых диаграмм (районы).
const ROW_H = 28
const ROW_LABEL_W = 222
const ROW_VALUE_W = 96
const ROW_AXIS_H = 50 // шкала значений + подпись единицы под рядами

type SeriesRef = { name: string; color: string }

function ChartCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-4">
      <h3 className="text-base font-semibold text-gray-900 mb-3">{title}</h3>
      {children}
    </section>
  )
}

function EmptyChart({ title }: { title: string }) {
  return (
    <ChartCard title={title}>
      <div className="flex h-44 items-center justify-center rounded-lg border border-dashed border-gray-300 text-base text-gray-400">
        Нет данных
      </div>
    </ChartCard>
  )
}

function ChartLegend({ items, dashed }: { items: SeriesRef[]; dashed?: boolean }) {
  return (
    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1">
      {items.map((it) => (
        <span key={it.name} className="inline-flex items-center gap-1.5 text-sm text-gray-600">
          {dashed ? (
            <span
              className="inline-block h-0 w-4 border-t-2 border-dashed"
              style={{ borderColor: it.color }}
            />
          ) : (
            <span className="inline-block h-3 w-3 rounded-sm" style={{ backgroundColor: it.color }} />
          )}
          {it.name}
        </span>
      ))}
    </div>
  )
}

/** Подписи годов под осью X (прорежены до ~12 меток). */
function YearLabels({ labels, xOf }: { labels: string[]; xOf: (i: number) => number }) {
  const step = Math.max(1, Math.ceil(labels.length / 12))
  // Последний год подписываем всегда; метку ближе шага к нему пропускаем, чтобы не слиплись.
  const last = labels.length - 1
  return (
    <>
      {labels.map((label, i) =>
        i === last || (i % step === 0 && last - i >= step) ? (
          <text
            key={label}
            x={xOf(i)}
            y={H - PAD_B + 20}
            textAnchor="middle"
            style={{ fontSize: AXIS_FONT, fill: '#6b7280' }}
          >
            {label}
          </text>
        ) : null,
      )}
    </>
  )
}

const innerW = (padL: number, padR = PAD_R) => W - padL - padR
const INNER_H = H - PAD_T - PAD_B
const yPx = (scale: Scale) => (v: number) =>
  PAD_T + (1 - (v - scale.min) / (scale.max - scale.min)) * INNER_H
const withUnit = (v: number, unit?: string, fmt: (v: number) => string = formatValue) =>
  `${fmt(v)}${unit ? ' ' + unit : ''}`
const NORM_LABEL = 'Среднемноголетнее значение'

// ── Столбчатая «Динамика по годам» ────────────────────────────────────

/** Общие параметры диаграмм динамики. */
interface DynamicsProps {
  title: string
  data: { year: string; value: number }[]
  color: string
  unit?: string
  /** Пунктирная линия линейного тренда по точкам ряда. */
  showTrend?: boolean
  /** Среднемноголетняя норма — горизонтальная пунктирная линия. */
  norm?: number
  /** Подпись нормы в легенде (с базовым периодом). */
  normLabel?: string
  /** Число знаков после запятой; без него — formatValue (с «тыс.»/«млн»). */
  decimals?: number
  /** Ряд отклонений: столбцы ниже нуля и выше нуля — разными цветами, нулевая линия. */
  diverging?: boolean
}

export function VerticalBarChart({
  title,
  data,
  color,
  unit,
  showTrend,
  norm,
  normLabel = NORM_LABEL,
  decimals,
  diverging,
}: DynamicsProps) {
  if (data.length === 0) return <EmptyChart title={title} />

  const fmt = valueFormatter(decimals)
  const ys = data.map((d) => d.value)
  const hasNorm = typeof norm === 'number' && Number.isFinite(norm)
  // Норму включаем в домен, чтобы её линия была видна, даже если она вне баров.
  const domain = hasNorm ? [...ys, norm as number] : ys
  const scale = niceScale(Math.min(0, ...domain), Math.max(0, ...domain))
  const padL = yAxisPad(scale, true)
  const yToPx = yPx(scale)
  const step = innerW(padL) / data.length
  const barW = Math.max(2, step * 0.7)
  const center = (i: number) => padL + step * (i + 0.5)
  const zeroY = yToPx(0)

  const trend = showTrend ? linearTrend(ys) : null
  const legend: SeriesRef[] = []
  if (trend) legend.push({ name: 'Тренд', color: TREND_COLOR })
  if (hasNorm) legend.push({ name: normLabel, color: NORM_COLOR })

  return (
    <ChartCard title={title}>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
        <YAxis scale={scale} x={padL} top={PAD_T} height={INNER_H} gridTo={W - PAD_R} unit={unitLabel(unit)} />
        {data.map((d, i) => {
          const y = yToPx(d.value)
          const fill = diverging ? (d.value < 0 ? DIVERGING_NEG : DIVERGING_POS) : color
          return (
            <rect
              key={d.year}
              x={center(i) - barW / 2}
              y={Math.min(y, zeroY)}
              width={barW}
              height={Math.abs(zeroY - y)}
              fill={fill}
              opacity={0.85}
            >
              <title>{`${d.year}: ${withUnit(d.value, unit, fmt)}`}</title>
            </rect>
          )
        })}
        {diverging && (
          <line x1={padL} x2={W - PAD_R} y1={zeroY} y2={zeroY} stroke="#475569" strokeWidth={1.2} />
        )}
        <YearLabels labels={data.map((d) => d.year)} xOf={center} />
        {hasNorm && (
          <line
            x1={padL}
            x2={W - PAD_R}
            y1={yToPx(norm as number)}
            y2={yToPx(norm as number)}
            stroke={NORM_COLOR}
            strokeWidth={1.5}
            strokeDasharray="6,3"
          >
            <title>{`${normLabel}: ${withUnit(norm as number, unit, fmt)}`}</title>
          </line>
        )}
        {trend && (
          <line
            x1={center(0)}
            x2={center(data.length - 1)}
            y1={yToPx(trend.yStart)}
            y2={yToPx(trend.yEnd)}
            stroke={TREND_COLOR}
            strokeWidth={2}
            strokeDasharray="5,4"
          >
            <title>{`Тренд: ${fmt(trend.yStart)} → ${fmt(trend.yEnd)}`}</title>
          </line>
        )}
      </svg>
      {diverging && (
        <ChartLegend
          items={[
            { name: 'Ниже нормы', color: DIVERGING_NEG },
            { name: 'Выше нормы', color: DIVERGING_POS },
          ]}
        />
      )}
      {legend.length > 0 && <ChartLegend items={legend} dashed />}
    </ChartCard>
  )
}

// ── Линейная «Динамика по годам» (одна линия + тренд + норма) ──────────

export function DynamicsLineChart({
  title,
  data,
  color,
  unit,
  showTrend,
  norm,
  normLabel = NORM_LABEL,
  decimals,
}: DynamicsProps) {
  if (data.length === 0) return <EmptyChart title={title} />

  const fmt = valueFormatter(decimals)
  const ys = data.map((d) => d.value)
  const hasNorm = typeof norm === 'number' && Number.isFinite(norm)
  const domain = hasNorm ? [...ys, norm as number] : ys
  const scale = niceScale(Math.min(0, ...domain), Math.max(0, ...domain))
  const padL = yAxisPad(scale, true)
  const yToPx = yPx(scale)
  const n = data.length
  const xToPx = (i: number) => padL + (n === 1 ? innerW(padL) / 2 : (i / (n - 1)) * innerW(padL))

  const linePath = data
    .map((d, i) => `${i === 0 ? 'M' : 'L'} ${xToPx(i)} ${yToPx(d.value)}`)
    .join(' ')

  const trend = showTrend ? linearTrend(ys) : null
  const legend: SeriesRef[] = []
  if (trend) legend.push({ name: 'Тренд', color: TREND_COLOR })
  if (hasNorm) legend.push({ name: normLabel, color: NORM_COLOR })

  return (
    <ChartCard title={title}>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
        <YAxis scale={scale} x={padL} top={PAD_T} height={INNER_H} gridTo={W - PAD_R} unit={unitLabel(unit)} />
        {scale.min < 0 && (
          <line x1={padL} x2={W - PAD_R} y1={yToPx(0)} y2={yToPx(0)} stroke="#94a3b8" strokeWidth={1} />
        )}
        {hasNorm && (
          <line
            x1={padL}
            x2={W - PAD_R}
            y1={yToPx(norm as number)}
            y2={yToPx(norm as number)}
            stroke={NORM_COLOR}
            strokeWidth={1.5}
            strokeDasharray="6,3"
          >
            <title>{`${normLabel}: ${withUnit(norm as number, unit, fmt)}`}</title>
          </line>
        )}
        <path d={linePath} fill="none" stroke={color} strokeWidth={2} />
        {data.map((d, i) => (
          <circle key={d.year} cx={xToPx(i)} cy={yToPx(d.value)} r={3.5} fill={color}>
            <title>{`${d.year}: ${withUnit(d.value, unit, fmt)}`}</title>
          </circle>
        ))}
        <YearLabels labels={data.map((d) => d.year)} xOf={xToPx} />
        {trend && (
          <line
            x1={xToPx(0)}
            x2={xToPx(n - 1)}
            y1={yToPx(trend.yStart)}
            y2={yToPx(trend.yEnd)}
            stroke={TREND_COLOR}
            strokeWidth={2}
            strokeDasharray="5,4"
          >
            <title>{`Тренд: ${fmt(trend.yStart)} → ${fmt(trend.yEnd)}`}</title>
          </line>
        )}
      </svg>
      {legend.length > 0 && <ChartLegend items={legend} dashed />}
    </ChartCard>
  )
}

// ── Несколько показателей по годам (линии) ────────────────────────────

export function MultiLineChart({
  title,
  data,
  series,
  unit,
}: {
  title: string
  data: { x: string; y: number; group: string }[]
  series: { id: string; name: string; color: string }[]
  unit?: string
}) {
  const colorByGroup = useMemo(() => {
    const m = new Map<string, string>()
    for (const s of series) m.set(s.name, s.color)
    return m
  }, [series])

  if (data.length === 0) return <EmptyChart title={title} />

  const xs = Array.from(new Set(data.map((d) => d.x))).sort()
  const ys = data.map((d) => d.y)
  const scale = niceScale(Math.min(0, ...ys), Math.max(0, ...ys))
  const padL = yAxisPad(scale, true)
  const yToPx = yPx(scale)
  const xToPx = (i: number) =>
    padL + (xs.length > 1 ? (i / (xs.length - 1)) * innerW(padL) : innerW(padL) / 2)

  const groups = series
    .map((s) => s.name)
    .filter((name) => data.some((d) => d.group === name))

  return (
    <ChartCard title={title}>
      <ChartLegend items={groups.map((name) => ({ name, color: colorByGroup.get(name) ?? '#64748b' }))} />
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
        <YAxis scale={scale} x={padL} top={PAD_T} height={INNER_H} gridTo={W - PAD_R} unit={unitLabel(unit)} />
        {/* Нулевая линия (ест. прирост может быть отрицательным) */}
        {scale.min < 0 && (
          <line x1={padL} x2={W - PAD_R} y1={yToPx(0)} y2={yToPx(0)} stroke="#94a3b8" strokeWidth={1} />
        )}
        <YearLabels labels={xs} xOf={xToPx} />
        {groups.map((name) => {
          const color = colorByGroup.get(name) ?? '#64748b'
          const pts = data
            .filter((d) => d.group === name)
            .sort((a, b) => a.x.localeCompare(b.x))
            .map((d) => ({ ix: xs.indexOf(d.x), y: d.y }))
            .filter((p) => p.ix >= 0)
          if (pts.length === 0) return null
          const path = pts
            .map((p, i) => `${i === 0 ? 'M' : 'L'}${xToPx(p.ix)},${yToPx(p.y)}`)
            .join(' ')
          return (
            <g key={name}>
              <path d={path} fill="none" stroke={color} strokeWidth={2} />
              {pts.map((p) => (
                <circle key={p.ix} cx={xToPx(p.ix)} cy={yToPx(p.y)} r={3.5} fill={color}>
                  <title>{`${name}, ${xs[p.ix]}: ${withUnit(p.y, unit)}`}</title>
                </circle>
              ))}
            </g>
          )
        })}
      </svg>
    </ChartCard>
  )
}

// ── Накопление по годам (столбцы) ─────────────────────────────────────

export function StackedVerticalBarChart({
  title,
  data,
  series,
  unit,
}: {
  title: string
  data: { label: string; parts: number[] }[]
  series: SeriesRef[]
  unit?: string
}) {
  if (data.length === 0) return <EmptyChart title={title} />

  const totals = data.map((d) => d.parts.reduce((a, b) => a + b, 0))
  const scale = niceScale(0, Math.max(...totals, 0))
  const padL = yAxisPad(scale, true)
  const yToPx = yPx(scale)
  const step = innerW(padL) / data.length
  const barW = Math.max(2, step * 0.7)
  const center = (i: number) => padL + step * (i + 0.5)

  return (
    <ChartCard title={title}>
      <ChartLegend items={series} />
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
        <YAxis scale={scale} x={padL} top={PAD_T} height={INNER_H} gridTo={W - PAD_R} unit={unitLabel(unit)} />
        {data.map((d, i) => {
          let acc = 0
          return (
            <g key={d.label}>
              {d.parts.map((p, si) => {
                const y0 = yToPx(acc)
                acc += p
                const y1 = yToPx(acc)
                return (
                  <rect
                    key={si}
                    x={center(i) - barW / 2}
                    y={y1}
                    width={barW}
                    height={Math.max(0, y0 - y1)}
                    fill={series[si]?.color ?? '#64748b'}
                    opacity={0.9}
                  >
                    <title>{`${series[si]?.name}, ${d.label}: ${withUnit(p, unit)}`}</title>
                  </rect>
                )
              })}
            </g>
          )
        })}
        <YearLabels labels={data.map((d) => d.label)} xOf={center} />
      </svg>
    </ChartCard>
  )
}

// ── Столбцы + линия на правой оси ─────────────────────────────────────

export function ComboBarLineChart({
  title,
  bars,
  barColor,
  barLabel,
  barUnit,
  lineByYear,
  lineColor,
  lineLabel,
  lineUnit,
}: {
  title: string
  bars: { year: string; value: number }[]
  barColor: string
  barLabel: string
  barUnit?: string
  lineByYear: Map<string, number>
  lineColor: string
  lineLabel: string
  lineUnit?: string
}) {
  if (bars.length === 0) return <EmptyChart title={title} />

  const barScale = niceScale(0, Math.max(...bars.map((b) => b.value), 0))
  const lineVals = bars.map((b) => lineByYear.get(b.year)).filter((v): v is number => v != null)
  const lineScale = niceScale(0, Math.max(...lineVals, 0))
  const padL = yAxisPad(barScale, true)
  const padR = yAxisPad(lineScale, true)
  const yBar = yPx(barScale)
  const yLine = yPx(lineScale)
  const step = innerW(padL, padR) / bars.length
  const barW = Math.max(2, step * 0.6)
  const center = (i: number) => padL + step * (i + 0.5)
  const baseY = PAD_T + INNER_H

  const linePts = bars
    .map((b, i) => ({ i, v: lineByYear.get(b.year) }))
    .filter((p): p is { i: number; v: number } => p.v != null)
  const linePath = linePts
    .map((p, k) => `${k === 0 ? 'M' : 'L'}${center(p.i)},${yLine(p.v)}`)
    .join(' ')

  return (
    <ChartCard title={title}>
      <ChartLegend items={[{ name: barLabel, color: barColor }, { name: lineLabel, color: lineColor }]} />
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
        <YAxis scale={barScale} x={padL} top={PAD_T} height={INNER_H} gridTo={W - padR} unit={unitLabel(barUnit)} />
        {linePts.length > 0 && (
          <YAxis
            scale={lineScale}
            x={W - padR}
            top={PAD_T}
            height={INNER_H}
            side="right"
            color={lineColor}
            unit={unitLabel(lineUnit)}
          />
        )}
        {bars.map((b, i) => (
          <rect
            key={b.year}
            x={center(i) - barW / 2}
            y={yBar(b.value)}
            width={barW}
            height={Math.max(0, baseY - yBar(b.value))}
            fill={barColor}
            opacity={0.85}
          >
            <title>{`${barLabel}, ${b.year}: ${withUnit(b.value, barUnit)}`}</title>
          </rect>
        ))}
        <YearLabels labels={bars.map((b) => b.year)} xOf={center} />
        {linePath && <path d={linePath} fill="none" stroke={lineColor} strokeWidth={2} />}
        {linePts.map((p) => (
          <circle key={p.i} cx={center(p.i)} cy={yLine(p.v)} r={3.5} fill={lineColor}>
            <title>{`${lineLabel}, ${bars[p.i].year}: ${withUnit(p.v, lineUnit)}`}</title>
          </circle>
        ))}
      </svg>
    </ChartCard>
  )
}

// ── Линейчатая диаграмма: районы за год ───────────────────────────────

export function HorizontalBarChart({
  title,
  data,
  highlightId,
  color,
  unit,
  decimals,
  diverging: forceDiverging,
}: {
  title: string
  data: { id: string; name: string; value: number }[]
  highlightId?: string
  color: string
  unit?: string
  /** Число знаков после запятой; без него — formatValue. */
  decimals?: number
  /** Отклонения: раскраска по знаку, даже если все значения одного знака. */
  diverging?: boolean
}) {
  if (data.length === 0) return <EmptyChart title={title} />
  const fmt = valueFormatter(decimals)

  const padL = ROW_LABEL_W + 8
  const rowsH = data.length * ROW_H
  const height = PAD_T + rowsH + ROW_AXIS_H
  const width = W - padL - ROW_VALUE_W
  const vals = data.map((d) => d.value)
  const scale = niceScale(Math.min(0, ...vals), Math.max(0, ...vals))
  const xToPx = (v: number) => padL + ((v - scale.min) / (scale.max - scale.min)) * width
  // Если есть значения обоих знаков (отклонение/аномалия) — расходящаяся
  // раскраска относительно нуля: дефицит ↔ профицит (замечание 4.3).
  const diverging = forceDiverging || (scale.min < 0 && scale.max > 0)
  const zeroX = xToPx(0)

  return (
    <ChartCard title={title}>
      <svg viewBox={`0 0 ${W} ${height}`} className="w-full h-auto">
        <XValueAxis
          scale={scale}
          y={PAD_T + rowsH}
          left={padL}
          width={width}
          gridTop={PAD_T}
          unit={unitLabel(unit)}
        />
        {data.map((d, i) => {
          const y = PAD_T + i * ROW_H
          const x1 = xToPx(d.value)
          const isSelected = d.id === highlightId
          const barFill = diverging ? (d.value < 0 ? DIVERGING_NEG : DIVERGING_POS) : color
          return (
            <g key={d.id}>
              <text
                x={ROW_LABEL_W}
                y={y + ROW_H / 2 + 5}
                textAnchor="end"
                className={isSelected ? 'fill-gray-900' : 'fill-gray-600'}
                style={{ fontSize: AXIS_FONT, fontWeight: isSelected ? 600 : 400 }}
              >
                {d.name}
              </text>
              <rect
                x={Math.min(zeroX, x1)}
                y={y + 4}
                width={Math.abs(x1 - zeroX)}
                height={ROW_H - 8}
                fill={barFill}
                opacity={isSelected ? 1 : 0.55}
                rx={3}
              >
                <title>{`${d.name}: ${withUnit(d.value, unit, fmt)}`}</title>
              </rect>
              <text
                x={W - ROW_VALUE_W + 6}
                y={y + ROW_H / 2 + 5}
                className="fill-gray-700"
                style={{ fontSize: AXIS_FONT, fontWeight: isSelected ? 600 : 400 }}
              >
                {fmt(d.value)}
              </text>
            </g>
          )
        })}
        {/* Вертикальная ось (ноль шкалы значений). */}
        <line x1={zeroX} x2={zeroX} y1={PAD_T} y2={PAD_T + rowsH} stroke="#94a3b8" strokeWidth={1} />
      </svg>
    </ChartCard>
  )
}

// ── Линейчатая с накоплением: районы за год ───────────────────────────

export function StackedHorizontalBarChart({
  title,
  data,
  series,
  highlightId,
  unit,
}: {
  title: string
  data: { id: string; name: string; parts: number[]; total: number }[]
  series: SeriesRef[]
  highlightId?: string
  unit?: string
}) {
  if (data.length === 0) return <EmptyChart title={title} />

  const padL = ROW_LABEL_W + 8
  const rowsH = data.length * ROW_H
  const height = PAD_T + rowsH + ROW_AXIS_H
  const width = W - padL - ROW_VALUE_W
  const scale = niceScale(0, Math.max(...data.map((d) => d.total), 0))
  const wOf = (v: number) => (v / scale.max) * width

  return (
    <ChartCard title={title}>
      <ChartLegend items={series} />
      <svg viewBox={`0 0 ${W} ${height}`} className="w-full h-auto">
        <XValueAxis
          scale={scale}
          y={PAD_T + rowsH}
          left={padL}
          width={width}
          gridTop={PAD_T}
          unit={unitLabel(unit)}
        />
        {data.map((d, i) => {
          const y = PAD_T + i * ROW_H
          const isSelected = d.id === highlightId
          let xacc = padL
          return (
            <g key={d.id}>
              <text
                x={ROW_LABEL_W}
                y={y + ROW_H / 2 + 5}
                textAnchor="end"
                className={isSelected ? 'fill-gray-900' : 'fill-gray-600'}
                style={{ fontSize: AXIS_FONT, fontWeight: isSelected ? 600 : 400 }}
              >
                {d.name}
              </text>
              {d.parts.map((p, si) => {
                const w = wOf(p)
                const x = xacc
                xacc += w
                return (
                  <rect
                    key={si}
                    x={x}
                    y={y + 4}
                    width={w}
                    height={ROW_H - 8}
                    fill={series[si]?.color ?? '#64748b'}
                    opacity={isSelected ? 1 : 0.7}
                  >
                    <title>{`${series[si]?.name}, ${d.name}: ${withUnit(p, unit)}`}</title>
                  </rect>
                )
              })}
              <text
                x={W - ROW_VALUE_W + 6}
                y={y + ROW_H / 2 + 5}
                className="fill-gray-700"
                style={{ fontSize: AXIS_FONT, fontWeight: isSelected ? 600 : 400 }}
              >
                {formatValue(d.total)}
              </text>
            </g>
          )
        })}
        <line x1={padL} x2={padL} y1={PAD_T} y2={PAD_T + rowsH} stroke="#94a3b8" strokeWidth={1} />
      </svg>
    </ChartCard>
  )
}
