'use client'

// Тепловая карта «район × год» (ТЗ 2.4.2: тепловые карты): цвет ячейки — значение
// показателя относительно минимума и максимума по всей таблице.

import { useMemo, useState } from 'react'

interface Props {
  title: string
  districts: { id: string; name: string }[]
  values: { region_id: string; period_start: string; value: number }[]
  format: (v: number) => string
  /** Чем больше, тем лучше — тёмный цвет у «хороших» значений не нужен, шкала одна. */
  color?: string
  onSelect?: (regionId: string, year: string) => void
}

function mix(hex: string, t: number): string {
  const n = parseInt(hex.slice(1), 16)
  const r = (n >> 16) & 255
  const g = (n >> 8) & 255
  const b = n & 255
  const k = 0.12 + 0.88 * t
  const c = (x: number) => Math.round(255 - (255 - x) * k)
  return `rgb(${c(r)},${c(g)},${c(b)})`
}

export default function DistrictYearHeatmap({ title, districts, values, format, color = '#2563eb', onSelect }: Props) {
  const [lastYears, setLastYears] = useState(10)
  const { years, cell, min, max } = useMemo(() => {
    const all = Array.from(new Set(values.map((v) => v.period_start.slice(0, 4)))).sort()
    const ys = all.slice(-lastYears)
    const m = new Map<string, number>()
    for (const v of values) {
      const y = v.period_start.slice(0, 4)
      if (ys.includes(y)) m.set(`${v.region_id}|${y}`, v.value)
    }
    const nums = Array.from(m.values())
    return { years: ys, cell: m, min: Math.min(...nums), max: Math.max(...nums) }
  }, [values, lastYears])

  if (years.length === 0 || districts.length === 0) return null
  const t = (v: number) => (max === min ? 0.5 : (v - min) / (max - min))

  return (
    <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-gray-900">{title}</h3>
        <label className="flex items-center gap-2 text-sm text-gray-500">
          Лет:
          <select
            value={lastYears}
            onChange={(e) => setLastYears(Number(e.target.value))}
            className="rounded border border-gray-300 px-2 py-0.5 text-sm"
          >
            {[5, 10, 15, 25].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-0.5 text-xs">
          <thead>
            <tr>
              <th className="sticky left-0 bg-white px-2 py-1 text-left font-medium text-gray-500">Район</th>
              {years.map((y) => (
                <th key={y} className="px-1 py-1 text-center font-medium text-gray-500">{y}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {districts.map((d) => (
              <tr key={d.id}>
                <td className="sticky left-0 whitespace-nowrap bg-white px-2 py-1 text-gray-700">{d.name}</td>
                {years.map((y) => {
                  const v = cell.get(`${d.id}|${y}`)
                  if (v === undefined) {
                    return <td key={y} className="rounded bg-gray-50 px-1 py-1 text-center text-gray-300">—</td>
                  }
                  const k = t(v)
                  return (
                    <td
                      key={y}
                      onClick={() => onSelect?.(d.id, y)}
                      title={`${d.name}, ${y}: ${format(v)}`}
                      className="cursor-pointer rounded px-1 py-1 text-center tabular-nums"
                      style={{ background: mix(color, k), color: k > 0.6 ? '#fff' : '#1f2937' }}
                    >
                      {format(v)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex items-center gap-2 text-xs text-gray-500">
        <span>{format(min)}</span>
        <span className="h-2 w-32 rounded" style={{ background: `linear-gradient(90deg, ${mix(color, 0)}, ${mix(color, 1)})` }} />
        <span>{format(max)}</span>
      </div>
    </section>
  )
}
