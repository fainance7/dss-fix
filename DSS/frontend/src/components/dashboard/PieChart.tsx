'use client'

import { useMemo } from 'react'

/**
 * Presentational pie/donut chart (inline SVG, no data fetching).
 * Fed by a parent block that computes segments from indicator_values —
 * mirrors how LineChart is a dumb renderer fed by LineBlock.
 */

export interface PieSegment {
  label: string
  value: number
  color: string
}

export interface PieChartProps {
  segments: PieSegment[]
  unit?: string
  /** Optional caption, e.g. the reference year. */
  year?: string
  /** 'donut' (default, center hole) matches IndicatorView style; 'pie' = full wedges. */
  variant?: 'pie' | 'donut'
  size?: number
}

function formatValue(v: number): string {
  const abs = Math.abs(v)
  const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2
  return v.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function polarToXY(cx: number, cy: number, r: number, angleDeg: number): [number, number] {
  const a = ((angleDeg - 90) * Math.PI) / 180
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)]
}

export default function PieChart({
  segments,
  unit,
  year,
  variant = 'donut',
  size = 220,
}: PieChartProps) {
  const total = useMemo(
    () => segments.reduce((s, seg) => s + (seg.value > 0 ? seg.value : 0), 0),
    [segments],
  )

  const arcs = useMemo(() => {
    if (total <= 0) return []
    const cx = size / 2
    const cy = size / 2
    const r = size / 2 - 4
    let acc = 0
    return segments
      .filter((seg) => seg.value > 0)
      .map((seg) => {
        const startAngle = (acc / total) * 360
        acc += seg.value
        const endAngle = (acc / total) * 360
        const [x1, y1] = polarToXY(cx, cy, r, startAngle)
        const [x2, y2] = polarToXY(cx, cy, r, endAngle)
        const largeArc = endAngle - startAngle > 180 ? 1 : 0
        const d = `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2} Z`
        return { seg, d, pct: (seg.value / total) * 100 }
      })
  }, [segments, total, size])

  if (segments.length === 0 || total <= 0) {
    return (
      <div className="rounded-lg border border-dashed border-gray-200 px-4 py-8 text-center text-base text-gray-400">
        Нет данных
      </div>
    )
  }

  const cx = size / 2
  const cy = size / 2
  const holeR = variant === 'donut' ? size * 0.22 : 0

  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label="Круговая диаграмма"
        className="flex-shrink-0"
      >
        {arcs.map((a, i) => (
          <path key={i} d={a.d} fill={a.seg.color} stroke="#fff" strokeWidth={1.5}>
            <title>{`${a.seg.label}: ${formatValue(a.seg.value)}${unit ? ' ' + unit : ''} (${a.pct.toFixed(0)}%)`}</title>
          </path>
        ))}
        {variant === 'donut' && <circle cx={cx} cy={cy} r={holeR} fill="#fff" />}
      </svg>

      <div className="min-w-0 flex-1">
        {year && <div className="mb-2 text-sm text-gray-400">За {year}</div>}
        <ul className="space-y-1.5">
          {arcs.map((a, i) => (
            <li key={i} className="flex items-center justify-between gap-3 text-base">
              <span className="flex min-w-0 items-center gap-2">
                <span
                  className="inline-block h-2.5 w-2.5 flex-shrink-0 rounded-sm"
                  style={{ background: a.seg.color }}
                />
                <span className="truncate text-gray-700">{a.seg.label}</span>
              </span>
              <span className="flex-shrink-0 font-semibold text-gray-900">
                {a.pct.toFixed(0)}%
                <span className="ml-1 font-normal text-gray-400">
                  {formatValue(a.seg.value)}
                  {unit ? ` ${unit}` : ''}
                </span>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
