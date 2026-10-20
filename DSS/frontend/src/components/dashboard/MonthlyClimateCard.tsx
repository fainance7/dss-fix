'use client'

import { Fragment, useEffect, useMemo, useState } from 'react'
import {
  fetchClimateMonthly,
  fetchClimateStations,
  type ClimateIndicatorKey,
  type ClimateMonthly,
  type ClimateStationsRef,
} from '@/lib/api/climate'
import { DATA_YEAR } from '@/lib/api/backend'
import { AXIS_FONT, YAxis, niceScale, unitLabel, yAxisPad } from './chartKit'

// Помесячный ход температуры или осадков по метеостанциям за выбранный год
// в сравнении с многолетней нормой того же месяца. Месяцы рассчитываются
// Системой из декад (см. backend/app/services/climate_dekads.py).

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const COLORS = { temperature: '#dc2626', precipitation: '#2563eb', norm: '#6b7280' }
const W = 900
const H = 320
const TOP = 16
const BOTTOM = 34

type Scope = 'oblast' | 'region' | 'station'

function fmt(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '—'
  return v.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export default function MonthlyClimateCard({
  title,
  indicator,
  description,
}: {
  title: string
  indicator: ClimateIndicatorKey
  description?: string
}) {
  const [ref, setRef] = useState<ClimateStationsRef | null>(null)
  const [scope, setScope] = useState<Scope>('oblast')
  const [targetId, setTargetId] = useState('')
  const [year, setYear] = useState<number | undefined>(undefined)
  const [data, setData] = useState<ClimateMonthly | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [openMonth, setOpenMonth] = useState<number | null>(null)

  useEffect(() => {
    fetchClimateStations().then(setRef).catch(() => setRef({ stations: [], regions: [] }))
  }, [])

  useEffect(() => {
    if (scope !== 'oblast' && !targetId) return
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchClimateMonthly({
      indicator,
      year,
      station_id: scope === 'station' ? targetId : undefined,
      region_id: scope === 'region' ? targetId : undefined,
    })
      .then((d) => {
        if (cancelled) return
        setData(d)
        if (year === undefined && d.year !== null) {
          // по умолчанию — последний год не позже отчётного
          const allowed = d.years.filter((y) => y <= DATA_YEAR)
          const last = allowed[allowed.length - 1]
          if (last !== undefined && last !== d.year) setYear(last)
        }
      })
      .catch(() => !cancelled && setError('Не удалось загрузить помесячные данные'))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [indicator, scope, targetId, year])

  const years = useMemo(() => (data?.years ?? []).filter((y) => y <= DATA_YEAR), [data])
  const isTemp = indicator === 'temperature'
  const color = COLORS[indicator]
  const unit = unitLabel(data?.indicator.unit)

  const chart = useMemo(() => {
    if (!data || data.months.length === 0) return null
    const vals = data.months.flatMap((m) => [m.value, m.norm]).filter((v): v is number => v !== null)
    if (vals.length === 0) return null
    const lo = isTemp ? Math.min(...vals) : 0
    const scale = niceScale(lo, Math.max(...vals))
    const pad = yAxisPad(scale, true)
    const plotW = W - pad - 12
    const plotH = H - TOP - BOTTOM
    const xOf = (i: number) => pad + (i + 0.5) * (plotW / 12)
    const yOf = (v: number) => TOP + (1 - (v - scale.min) / (scale.max - scale.min)) * plotH
    return { scale, pad, plotW, plotH, xOf, yOf, band: plotW / 12 }
  }, [data, isTemp])

  const basisNote =
    data?.scope.basis === 'nearest_station'
      ? `В районе нет метеостанции — значения по соседней станции «${data.scope.stations.join(', ')}»`
      : data?.scope.basis === 'district_mean'
        ? `Среднее по станциям района: ${data.scope.stations.join(', ')}`
        : data?.scope.basis === 'stations_mean'
          ? 'Среднее по всем метеостанциям области'
          : data?.scope.type === 'station'
            ? `Метеостанция ${data.scope.name} (${data.scope.region})`
            : ''

  return (
    <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-gray-900">{title}</h3>
          {description && <p className="mt-0.5 text-sm text-gray-500">{description}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <select
            value={scope}
            onChange={(e) => {
              const s = e.target.value as Scope
              setScope(s)
              setOpenMonth(null)
              setTargetId(
                s === 'station' ? ref?.stations[0]?.id ?? '' : s === 'region' ? ref?.regions[0]?.id ?? '' : '',
              )
            }}
            className="rounded-lg border border-gray-300 px-2 py-1.5"
            aria-label="Территория"
          >
            <option value="oblast">Область</option>
            <option value="region">Район</option>
            <option value="station">Метеостанция</option>
          </select>
          {scope !== 'oblast' && (
            <select
              value={targetId}
              onChange={(e) => {
                setTargetId(e.target.value)
                setOpenMonth(null)
              }}
              className="max-w-[220px] rounded-lg border border-gray-300 px-2 py-1.5"
              aria-label={scope === 'station' ? 'Метеостанция' : 'Район'}
            >
              {(scope === 'station' ? ref?.stations ?? [] : ref?.regions ?? []).map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          )}
          <select
            value={year ?? data?.year ?? ''}
            onChange={(e) => setYear(Number(e.target.value))}
            className="rounded-lg border border-gray-300 px-2 py-1.5"
            aria-label="Год"
          >
            {[...years].reverse().map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
      {loading && !data && <p className="text-sm text-gray-400">Загрузка…</p>}
      {data && !chart && !loading && <p className="text-sm text-gray-400">Нет данных за выбранный период</p>}

      {data && chart && (
        <>
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={title}>
            <YAxis
              scale={chart.scale}
              x={chart.pad}
              top={TOP}
              height={chart.plotH}
              gridTo={chart.pad + chart.plotW}
              unit={unit}
            />
            {isTemp && chart.scale.min < 0 && chart.scale.max > 0 && (
              <line x1={chart.pad} x2={chart.pad + chart.plotW} y1={chart.yOf(0)} y2={chart.yOf(0)} stroke="#9ca3af" />
            )}
            {/* значения года */}
            {isTemp ? (
              <polyline
                fill="none"
                stroke={color}
                strokeWidth={2.5}
                points={data.months
                  .map((m, i) => (m.value !== null ? `${chart.xOf(i)},${chart.yOf(m.value)}` : null))
                  .filter(Boolean)
                  .join(' ')}
              />
            ) : (
              data.months.map((m, i) =>
                m.value !== null ? (
                  <rect
                    key={i}
                    x={chart.xOf(i) - chart.band * 0.3}
                    width={chart.band * 0.6}
                    y={chart.yOf(Math.max(m.value, 0))}
                    height={Math.max(1, chart.yOf(0) - chart.yOf(Math.max(m.value, 0)))}
                    fill={color}
                    opacity={m.status === 'approved' ? 0.85 : 0.45}
                  >
                    <title>{`${MONTHS[i]}: ${fmt(m.value)} ${unit}`}</title>
                  </rect>
                ) : null,
              )
            )}
            {isTemp &&
              data.months.map((m, i) =>
                m.value !== null ? (
                  <circle key={i} cx={chart.xOf(i)} cy={chart.yOf(m.value)} r={4} fill={color}>
                    <title>{`${MONTHS[i]}: ${fmt(m.value)} ${unit}`}</title>
                  </circle>
                ) : null,
              )}
            {/* норма */}
            {data.months.map((m, i) =>
              m.norm !== null ? (
                <line
                  key={`n${i}`}
                  x1={chart.xOf(i) - chart.band * 0.38}
                  x2={chart.xOf(i) + chart.band * 0.38}
                  y1={chart.yOf(m.norm)}
                  y2={chart.yOf(m.norm)}
                  stroke={COLORS.norm}
                  strokeWidth={2}
                  strokeDasharray="5,3"
                >
                  <title>{`Норма ${MONTHS[i]}: ${fmt(m.norm)} ${unit}`}</title>
                </line>
              ) : null,
            )}
            {data.months.map((m, i) => (
              <Fragment key={`x${i}`}>
                <text
                  x={chart.xOf(i)}
                  y={H - 12}
                  textAnchor="middle"
                  style={{ fontSize: AXIS_FONT, fill: m.value === null ? '#d1d5db' : '#4b5563' }}
                >
                  {MONTHS[i]}
                </text>
              </Fragment>
            ))}
          </svg>

          <div className="mt-2 flex flex-wrap gap-4 text-sm text-gray-600">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-4 rounded-sm" style={{ background: color }} />
              {data.year} г.
            </span>
            {data.base_period && (
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-4 border-t-2 border-dashed" style={{ borderColor: COLORS.norm }} />
                многолетнее среднее {data.base_period[0]}–{data.base_period[1]} гг.
              </span>
            )}
            {basisNote && <span className="text-gray-500">{basisNote}</span>}
          </div>
          {data.months.some((m) => (m.substitutes?.length ?? 0) > 0) && (
            <p className="mt-1 text-xs text-amber-700">
              Часть декад {data.year} г. восполнена в исходных данных по соседним станциям:{' '}
              {Array.from(new Set(data.months.flatMap((m) => m.substitutes ?? []).map((x) => x.replace(/ \(\d+ дек\.\)$/, '')))).join('; ')}.
            </p>
          )}

          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500">
                  <th className="py-1 pr-3 font-medium">Месяц</th>
                  <th className="py-1 pr-3 text-right font-medium">{data.year} г.</th>
                  <th className="py-1 pr-3 text-right font-medium">Норма</th>
                  <th className="py-1 pr-3 text-right font-medium">Отклонение</th>
                  <th className="py-1 font-medium" />
                </tr>
              </thead>
              <tbody>
                {data.months.map((m, i) => (
                  <Fragment key={m.month}>
                    <tr className="border-t border-gray-100">
                      <td className="py-1 pr-3">{MONTHS[i]}</td>
                      <td className="py-1 pr-3 text-right font-mono">{fmt(m.value)}</td>
                      <td className="py-1 pr-3 text-right font-mono text-gray-500">{fmt(m.norm)}</td>
                      <td
                        className={`py-1 pr-3 text-right font-mono ${
                          m.anomaly === null
                            ? 'text-gray-400'
                            : isTemp
                              ? m.anomaly > 0 ? 'text-red-600' : 'text-blue-600'
                              : m.anomaly > 0 ? 'text-blue-600' : 'text-amber-700'
                        }`}
                      >
                        {m.anomaly === null ? '—' : `${m.anomaly > 0 ? '+' : ''}${fmt(m.anomaly)}`}
                      </td>
                      <td className="py-1 text-xs text-gray-500">
                        {m.value === null && data.scope.type === 'station' && (m.dekads?.length ?? 0) > 0
                          ? `месяц не рассчитан: ${m.dekads?.length} из 3 декад`
                          : m.value === null
                            ? 'нет данных'
                            : m.status !== 'approved'
                              ? 'на проверке'
                              : ''}
                        {(m.substitutes?.length ?? 0) > 0 && (
                          <span className="ml-1 text-amber-700" title={m.substitutes?.join('\n')}>
                            частично по соседней станции
                          </span>
                        )}
                        {data.scope.type === 'station' && (m.dekads?.length ?? 0) > 0 && (
                          <button
                            type="button"
                            onClick={() => setOpenMonth(openMonth === m.month ? null : m.month)}
                            className="ml-2 text-primary-700 hover:underline"
                          >
                            {openMonth === m.month ? 'скрыть декады' : 'декады'}
                          </button>
                        )}
                      </td>
                    </tr>
                    {openMonth === m.month && (
                      <tr className="bg-gray-50 text-xs text-gray-600">
                        <td />
                        <td colSpan={4} className="py-1">
                          {(m.dekads ?? []).map((d) => (
                            <span key={d.dekad} className="mr-4">
                              {d.dekad}-я декада: <b className="font-mono">{fmt(d.value)}</b> {unit}
                              {d.derivation && <span className="text-amber-700"> ({d.derivation})</span>}
                            </span>
                          ))}
                          <span className="text-gray-400">
                            {isTemp ? '· месяц — среднее, взвешенное по дням' : '· месяц — сумма декад'}
                          </span>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}
