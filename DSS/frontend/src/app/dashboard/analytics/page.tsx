'use client'

import { useEffect, useMemo, useState } from 'react'
import { useAuthStore } from '@/store/authStore'
import { createForecast, fetchActiveModels, type AiForecast, type AiModel } from '@/lib/api/ai'
import {
  formatNumber,
  latestYear,
  normalizePopulation,
  sortedYears,
  useOverviewIndicators,
  type OverviewSpec,
  type YearSeries,
} from '@/lib/indicators/overview'
import { AXIS_FONT, YAxis, niceScale, yAxisPad } from '@/components/dashboard/chartKit'
import { DATA_YEAR } from '@/lib/api/backend'

// Показатели с данными по всем 14 районам (ВРП публикуется только по области — он на Дашборде).
interface Chip {
  spec: OverviewSpec
  label: string
  unit: string
  scale: number // отображаемое значение = исходное / scale
  digits: number
  rate?: boolean // доля/уровень: изменение в п.п., область не суммируется
  // Ряд с дефектом единиц в источнике — прогноз по нему недостоверен до исправления данных.
  forecastUnsafe?: boolean
  // Моментный показатель (на конец года) — год подписывается как «на начало следующего».
  pointInTime?: boolean
  color: string
}

const CHIPS: Chip[] = [
  {
    spec: {
      key: 'population', direction: 'social_demog', additive: true, higherIsBetter: true, exact: true,
      // Для динамики — непрерывный ряд «на конец года» (1991–2024); у «(всего)»
      // по области после отклонения дефектных лет остались только крайние годы.
      candidates: ['Численность населения на конец года', 'Численность населения (всего)'],
      // Численность на 01.01.2026 — значение на конец отчётного 2025 года.
      startOfYear: ['Численность населения (всего)'],
      normalize: normalizePopulation,
    },
    label: 'Население', unit: 'тыс. чел.', scale: 1000, digits: 1, forecastUnsafe: true, pointInTime: true, color: '#3b82f6',
  },
  {
    spec: { key: 'investment', direction: 'economy', additive: true, higherIsBetter: true, candidates: ['Объем инвестиций в основной капитал'] },
    label: 'Инвестиции', unit: 'млрд ₸', scale: 1, digits: 1, color: '#8b5cf6',
  },
  {
    spec: { key: 'unemployment', direction: 'labor', higherIsBetter: false, candidates: ['Уровень безработицы'] },
    label: 'Безработица', unit: '%', scale: 1, digits: 1, rate: true, color: '#f59e0b',
  },
  {
    spec: { key: 'wage', direction: 'labor', higherIsBetter: true, exact: true, candidates: ['Среднемесячная номинальная заработная плата'] },
    label: 'Зарплата', unit: '₸', scale: 1, digits: 0, color: '#10b981',
  },
  {
    spec: { key: 'agro', direction: 'economy', additive: true, higherIsBetter: true, exact: true, candidates: ['Валовая продукция сельского хозяйства'] },
    label: 'Продукция СХ', unit: 'млрд ₸', scale: 1, digits: 1, color: '#22c55e',
  },
  {
    spec: { key: 'industry', direction: 'economy', additive: true, higherIsBetter: true, exact: true, candidates: ['Валовая продукция промышленности'] },
    label: 'Продукция промышленности', unit: 'млрд ₸', scale: 1, digits: 1, color: '#ef4444',
  },
]
const SPECS = CHIPS.map((c) => c.spec)
const COMPARE_COLORS = ['#6366f1', '#22c55e', '#f59e0b', '#ef4444', '#8b5cf6']
const TABLE_YEARS = 6

interface Pt {
  year: number
  value: number
  low?: number
  high?: number
  forecast?: boolean
}

// Динамика области + (опц.) прогноз пунктиром с интервалом.
function TrendChart({ points, color, unit, digits, axisEnd }: {
  points: Pt[]
  color: string
  unit: string
  digits: number
  /** Ось X продолжается до этого года, даже если точек за него нет. */
  axisEnd?: number
}) {
  const [hover, setHover] = useState<Pt | null>(null)
  if (points.length < 2) {
    return <div className="flex h-40 items-center justify-center text-base text-gray-400">нет данных по области</div>
  }
  const W = 560
  const H = 230
  const PAD_R = 18
  const PAD_T = 14
  const PAD_B = 30
  const all = points.flatMap((p) => [p.value, p.low ?? p.value, p.high ?? p.value])
  const scale = niceScale(Math.min(...all), Math.max(...all))
  const PAD_L = yAxisPad(scale, false)
  const y0 = points[0].year
  const y1 = Math.max(points[points.length - 1].year, axisEnd ?? 0)
  const axis = Array.from({ length: y1 - y0 + 1 }, (_, k) => y0 + k)
  const x = (yr: number) => PAD_L + ((yr - y0) / Math.max(1, y1 - y0)) * (W - PAD_L - PAD_R)
  const y = (v: number) => PAD_T + ((scale.max - v) / (scale.max - scale.min)) * (H - PAD_T - PAD_B)
  const fact = points.filter((p) => !p.forecast)
  const fc = points.filter((p) => p.forecast)
  const lastFact = fact[fact.length - 1]
  // Годы оси без факта и без прогноза (статистика ещё не опубликована).
  const missing = axis.filter((yr) => yr > lastFact.year && !fc.some((p) => p.year === yr))
  const line = (ps: Pt[]) => ps.map((p, k) => `${k ? 'L' : 'M'}${x(p.year)},${y(p.value)}`).join(' ')
  const band = fc.length
    ? `M${x(lastFact.year)},${y(lastFact.value)} ` +
      fc.map((p) => `L${x(p.year)},${y(p.high ?? p.value)}`).join(' ') + ' ' +
      [...fc].reverse().map((p) => `L${x(p.year)},${y(p.low ?? p.value)}`).join(' ') + ' Z'
    : ''
  const step = Math.ceil(axis.length / 9)
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" onMouseLeave={() => setHover(null)}>
        <YAxis scale={scale} x={PAD_L} top={PAD_T} height={H - PAD_T - PAD_B} gridTo={W - PAD_R} />
        {band && <path d={band} fill={color} opacity={0.12} />}
        <path d={line(fact)} fill="none" stroke={color} strokeWidth="2.5" strokeLinejoin="round" />
        {fc.length > 0 && (
          <path d={line([lastFact, ...fc])} fill="none" stroke={color} strokeWidth="2" strokeDasharray="6 4" />
        )}
        {points.map((p) => (
          <circle
            key={p.year}
            cx={x(p.year)}
            cy={y(p.value)}
            r={3.5}
            fill={p.forecast ? color : 'white'}
            stroke={color}
            strokeWidth="1.5"
            className="cursor-pointer"
            onMouseEnter={() => setHover(p)}
          />
        ))}
        {axis.map((yr, i) =>
          (i % step === 0 && axis.length - 1 - i >= step) || i === axis.length - 1 ? (
            <text key={yr} x={x(yr)} y={H - 8} textAnchor="middle" fontSize={AXIS_FONT} fill="#6b7280">
              {yr}
            </text>
          ) : null,
        )}
      </svg>
      {missing.length > 0 && (
        <p className="mt-1 text-right text-sm text-gray-400">
          {missing.join(', ')}: данные не опубликованы
        </p>
      )}
      {hover && (
        <div className="pointer-events-none absolute right-2 top-0 rounded-lg bg-gray-900 px-2.5 py-1.5 text-sm text-white shadow-lg">
          <div className="font-semibold">
            {hover.year}
            {hover.forecast ? ' · прогноз' : ''}
          </div>
          <div className="text-gray-300">
            {formatNumber(hover.value, digits)} {unit}
            {hover.low !== undefined && hover.high !== undefined && (
              <> ({formatNumber(hover.low, digits)}–{formatNumber(hover.high, digits)})</>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// Единица измерения — в заголовке карточки, у значений её не повторяем.
function DistrictBars({ rows, color, digits }: {
  rows: { id: string; name: string; value: number }[]
  color: string
  digits: number
}) {
  if (rows.length === 0) return <div className="py-10 text-center text-base text-gray-400">нет данных за выбранный год</div>
  const max = Math.max(...rows.map((r) => Math.abs(r.value))) || 1
  return (
    <div className="space-y-1.5">
      {rows.map((r) => (
        <div key={r.id} className="flex items-center gap-2 text-sm">
          <span className="w-40 shrink-0 truncate text-gray-600" title={r.name}>{r.name}</span>
          <div className="h-4 flex-1 rounded bg-gray-50">
            <div className="h-4 rounded" style={{ width: `${(Math.abs(r.value) / max) * 100}%`, backgroundColor: color, opacity: 0.8 }} />
          </div>
          <span className="w-20 shrink-0 text-right tabular-nums text-gray-700">
            {formatNumber(r.value, digits)}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function AnalyticsPage() {
  const { hasRole } = useAuthStore()
  const isAkimRay = hasRole('akim_ray') && !hasRole('admin') && !hasRole('akim_obl')
  const canForecast = hasRole('exp_dir') || hasRole('admin') || hasRole('akim_obl')
  const { data, regions, loading } = useOverviewIndicators(SPECS)

  const [chipKey, setChipKey] = useState(CHIPS[0].spec.key)
  const [year, setYear] = useState<number | null>(null)
  const [compare, setCompare] = useState<string[]>([])
  const [models, setModels] = useState<AiModel[] | null>(null)
  const [forecasts, setForecasts] = useState<Record<string, AiForecast>>({})
  const [forecasting, setForecasting] = useState(false)
  const [forecastError, setForecastError] = useState<string | null>(null)

  const chip = CHIPS.find((c) => c.spec.key === chipKey)!
  const ind = data[chipKey]
  const scaled = (v: number) => v / chip.scale

  const districtsList = useMemo(
    () => regions.filter((r) => r.level === 2).sort((a, b) => a.name.localeCompare(b.name, 'ru')),
    [regions],
  )

  // Годы с районными данными (для выбора года и таблицы).
  const years = useMemo(() => {
    const ys = new Set<number>()
    ind?.districts.forEach((s) => s.forEach((_, y) => ys.add(y)))
    return Array.from(ys).sort((a, b) => a - b)
  }, [ind])

  // При смене показателя — последний год и топ-3 районов для сравнения.
  useEffect(() => {
    if (!ind || years.length === 0) return
    const y = years[years.length - 1]
    setYear(y)
    const top = districtsList
      .map((r) => ({ id: r.id, v: ind.districts.get(r.id)?.get(y) }))
      .filter((r): r is { id: string; v: number } => r.v !== undefined)
      .sort((a, b) => (chip.spec.higherIsBetter === false ? a.v - b.v : b.v - a.v))
      .slice(0, 3)
      .map((r) => r.id)
    setCompare(top)
    setForecastError(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chipKey, ind, years.length])

  const barRows = useMemo(() => {
    if (!ind || year === null) return []
    return districtsList
      .map((r) => ({ id: r.id, name: r.name, value: ind.districts.get(r.id)?.get(year) }))
      .filter((r): r is { id: string; name: string; value: number } => r.value !== undefined)
      .map((r) => ({ ...r, value: scaled(r.value) }))
      .sort((a, b) => (chip.spec.higherIsBetter === false ? a.value - b.value : b.value - a.value))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ind, year, districtsList, chipKey])

  // Аким района видит только свой район (бэкенд отдаёт одни его значения) —
  // основной ряд экрана тогда его район, а не область.
  const ownDistrictId = isAkimRay && ind && ind.districts.size === 1 ? Array.from(ind.districts.keys())[0] : null
  const mainSeries: YearSeries | undefined = ind ? (ownDistrictId ? ind.districts.get(ownDistrictId) : ind.oblast) : undefined
  const mainLabel = ownDistrictId ? districtsList.find((r) => r.id === ownDistrictId)?.name ?? 'Ваш район' : 'Область'
  const oblastValue = mainSeries && year !== null ? mainSeries.get(year) : undefined
  const oblastPrev = mainSeries && year !== null ? mainSeries.get(year - 1) : undefined
  const change =
    oblastValue !== undefined && oblastPrev !== undefined
      ? chip.rate
        ? scaled(oblastValue) - scaled(oblastPrev)
        : oblastPrev
          ? ((oblastValue - oblastPrev) / Math.abs(oblastPrev)) * 100
          : null
      : null
  const changeGood =
    change === null || Math.abs(change) < 0.05 ? null : chip.spec.higherIsBetter === false ? change <= 0 : change >= 0

  const model = useMemo(
    () => (models && ind?.ind ? models.find((m) => m.indicator_id === ind.ind!.id) ?? null : null),
    [models, ind],
  )
  const forecast = forecasts[chipKey]

  const trendPoints = useMemo<Pt[]>(() => {
    if (!ind || !mainSeries) return []
    const fact: Pt[] = sortedYears(mainSeries)
      .filter((y) => y >= 2010)
      .map((y) => ({ year: y, value: scaled(mainSeries.get(y)!) }))
    if (!forecast?.result_values) return fact
    const lastYear = fact.length ? fact[fact.length - 1].year : 0
    const fc: Pt[] = Object.entries(forecast.result_values)
      .map(([d, v]) => {
        const ci = forecast.confidence_scores?.[d]
        return {
          year: Number(d.slice(0, 4)),
          value: scaled(v),
          low: ci ? scaled(ci.low) : undefined,
          high: ci ? scaled(ci.high) : undefined,
          forecast: true,
        }
      })
      .filter((p) => p.year > lastYear)
      .sort((a, b) => a.year - b.year)
    return [...fact, ...fc]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ind, forecast, chipKey, mainSeries])

  async function runForecast() {
    if (!ind?.ind) return
    setForecasting(true)
    setForecastError(null)
    try {
      const list = models ?? (await fetchActiveModels())
      if (!models) setModels(list)
      const m = list.find((x) => x.indicator_id === ind.ind!.id)
      if (!m) {
        setForecastError('Для показателя нет обученной модели')
        return
      }
      const fc = await createForecast({ model_id: m.id, indicator_id: ind.ind.id, horizon_years: 3 })
      setForecasts((prev) => ({ ...prev, [chipKey]: fc }))
    } catch {
      setForecastError('Не удалось построить прогноз')
    } finally {
      setForecasting(false)
    }
  }

  // Модели нужны для подписи качества; грузим один раз при первом показе кнопки.
  useEffect(() => {
    if (canForecast && models === null) fetchActiveModels().then(setModels)
  }, [canForecast, models])

  const compareSeries = useMemo(() => {
    if (!ind) return []
    return compare
      .map((id) => ({ id, name: districtsList.find((r) => r.id === id)?.name ?? '', series: ind.districts.get(id) }))
      .filter((c): c is { id: string; name: string; series: YearSeries } => !!c.series)
  }, [compare, ind, districtsList])
  // Графики и таблица — до отчётного года включительно, даже если данных за него нет.
  const axisYears = useMemo(() => {
    if (years.length === 0) return years
    const out = [...years]
    for (let y = years[years.length - 1] + 1; y <= DATA_YEAR; y += 1) out.push(y)
    return out
  }, [years])
  const compareYears = axisYears.slice(-10)

  const tableYears = axisYears.slice(-TABLE_YEARS)
  const mape = model?.metrics?.mape as number | undefined
  const interval = forecast?.confidence_scores ? Object.values(forecast.confidence_scores)[0] : undefined

  return (
    <>
      {/* Вкладки — всегда отдельной строкой под заголовком: длина подписи источника
          не должна переносить их то вправо, то вниз при переключении показателя. */}
      <div className="mb-6 space-y-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Аналитика</h1>
          <p className="mt-0.5 text-base text-gray-500">
            {isAkimRay ? 'Показатели вашего района' : 'Показатели развития районов СКО'}
            {ind?.ind && <span className="text-gray-400"> · источник: {ind.ind.name}</span>}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {CHIPS.map((c) => (
            <button
              key={c.spec.key}
              onClick={() => setChipKey(c.spec.key)}
              className={`rounded-lg border px-3 py-1.5 text-base font-medium transition-colors ${
                chipKey === c.spec.key
                  ? 'border-primary-500 bg-primary-600 text-white'
                  : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:text-gray-900'
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-20 animate-pulse rounded-xl bg-white ring-1 ring-gray-100" />
          ))}
        </div>
      ) : !ind?.ind ? (
        <div className="rounded-xl bg-white p-10 text-center text-base text-gray-400 ring-1 ring-gray-100">
          Показатель «{chip.label}» не найден в справочнике
        </div>
      ) : (
        <>
          <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
            {[
              {
                label: `${mainLabel}, ${year === null ? '' : chip.pointInTime ? `на начало ${year + 1}` : year}`,
                value: oblastValue !== undefined ? formatNumber(scaled(oblastValue), chip.digits) : '—',
                unit: oblastValue !== undefined ? chip.unit : '',
              },
              {
                label: 'К предыдущему году',
                value: change !== null ? `${change >= 0.05 ? '+' : ''}${formatNumber(Math.abs(change) < 0.05 ? 0 : change)}` : '—',
                unit: change !== null ? (chip.rate ? (chip.unit === '%' ? 'п.п.' : chip.unit) : '%') : '',
                good: changeGood,
              },
              ...(isAkimRay || barRows.length < 2
                ? []
                : [
                    { label: 'Лидер', value: barRows[0].name, unit: '' },
                    { label: 'Отстающий', value: barRows[barRows.length - 1].name, unit: '' },
                  ]),
            ].map((card) => (
              <div key={card.label} className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
                <div className="text-sm font-medium uppercase tracking-wide text-gray-500">{card.label}</div>
                <div
                  className={`mt-1.5 truncate text-xl font-bold ${
                    'good' in card && card.good !== null ? (card.good ? 'text-green-600' : 'text-red-600') : 'text-gray-900'
                  }`}
                  title={card.value}
                >
                  {card.value}
                  {card.unit && <span className="ml-1 text-base font-normal text-gray-400">{card.unit}</span>}
                </div>
              </div>
            ))}
          </div>

          <div className="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-base font-semibold text-gray-900">Динамика — {ownDistrictId ? mainLabel : 'СКО'} · {chip.label} ({chip.unit})</h2>
                {canForecast && !ownDistrictId && (
                  <button
                    onClick={runForecast}
                    disabled={forecasting || !!chip.forecastUnsafe}
                    title={chip.forecastUnsafe ? 'Прогноз недоступен: в исходном ряду смешаны единицы измерения' : undefined}
                    className="rounded-lg bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {forecasting ? 'Строим прогноз…' : forecast ? '◆ Обновить прогноз' : '◆ Прогноз на 3 года'}
                  </button>
                )}
              </div>
              <TrendChart points={trendPoints} color={chip.color} unit={chip.unit} digits={chip.digits} axisEnd={DATA_YEAR} />
              {forecastError && <p className="mt-2 text-sm text-red-600">{forecastError}</p>}
              {forecast && (
                <div className="mt-3 space-y-1 rounded-lg bg-gray-50 p-3 text-sm text-gray-600">
                  <div>
                    Модель: <b>{model?.model_type ?? '—'}</b>
                    {mape !== undefined && <> · ошибка на истории (MAPE) {formatNumber(mape)}%</>}
                    {interval && (
                      <>
                        {' '}· интервал{' '}
                        {interval.method === 'split-conformal'
                          ? `${Math.round((interval.level ?? 0.8) * 100)}% по ошибкам бэктеста`
                          : '±15% (оценочный)'}
                      </>
                    )}
                  </div>
                  {forecast.xai_explanation?.interpretation && (
                    <div className="text-gray-500">{forecast.xai_explanation.interpretation}</div>
                  )}
                </div>
              )}
            </div>

            <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-base font-semibold text-gray-900">По районам — {chip.label} ({chip.unit})</h2>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setYear((y) => (y === null ? y : years[Math.max(0, years.indexOf(y) - 1)]))}
                    className="rounded border border-gray-200 px-2 py-0.5 text-sm text-gray-500 hover:bg-gray-50"
                  >
                    ‹
                  </button>
                  <span className="w-10 text-center text-sm font-medium text-gray-700">{year ?? '—'}</span>
                  <button
                    onClick={() =>
                      setYear((y) => (y === null ? y : years[Math.min(years.length - 1, years.indexOf(y) + 1)]))
                    }
                    className="rounded border border-gray-200 px-2 py-0.5 text-sm text-gray-500 hover:bg-gray-50"
                  >
                    ›
                  </button>
                </div>
              </div>
              <DistrictBars rows={barRows} color={chip.color} digits={chip.digits} />
            </div>
          </div>

          {!isAkimRay && (
            <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold text-gray-900">Сравнение районов — {chip.label} ({chip.unit})</h2>
                  <p className="mt-0.5 text-sm text-gray-400">До 5 районов, последние 10 лет</p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {districtsList.map((d) => {
                    const idx = compare.indexOf(d.id)
                    return (
                      <button
                        key={d.id}
                        onClick={() =>
                          setCompare((prev) =>
                            prev.includes(d.id) ? prev.filter((x) => x !== d.id) : prev.length < 5 ? [...prev, d.id] : prev,
                          )
                        }
                        className={`rounded-full border px-2.5 py-0.5 text-sm font-medium transition-colors ${
                          idx >= 0 ? 'border-transparent text-white' : 'border-gray-200 text-gray-500 hover:border-gray-300'
                        }`}
                        style={idx >= 0 ? { backgroundColor: COMPARE_COLORS[idx] } : {}}
                      >
                        {d.name}
                      </button>
                    )
                  })}
                </div>
              </div>
              {compareSeries.length > 0 && compareYears.length > 1 ? (
                <CompareChart series={compareSeries} years={compareYears} scale={chip.scale} colors={compare} unit={chip.unit} />
              ) : (
                <p className="py-8 text-center text-base text-gray-400">Выберите хотя бы один район</p>
              )}
            </div>
          )}

          <div className="mt-6 overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
            <div className="border-b border-gray-100 px-5 py-4">
              <h2 className="text-base font-semibold text-gray-900">
                Таблица: {chip.label} по районам ({chip.unit})
              </h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-base">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">Район</th>
                    {tableYears.map((y) => (
                      <th
                        key={y}
                        className={`px-4 py-3 text-right text-sm font-medium ${y === year ? 'text-primary-600' : 'text-gray-500'}`}
                      >
                        {y}
                      </th>
                    ))}
                    <th className="px-4 py-3 text-right text-sm font-medium text-gray-500">Δ</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {districtsList
                    .filter((d) => ind.districts.has(d.id))
                    .map((d, idx) => {
                      const s = ind.districts.get(d.id)!
                      const present = tableYears.filter((y) => s.has(y))
                      const first = present.length ? scaled(s.get(present[0])!) : undefined
                      const last = present.length ? scaled(s.get(present[present.length - 1])!) : undefined
                      const delta =
                        first !== undefined && last !== undefined && present.length > 1
                          ? chip.rate
                            ? last - first
                            : first
                              ? ((last - first) / Math.abs(first)) * 100
                              : null
                          : null
                      const good =
                        delta === null || Math.abs(delta) < 0.05 ? null : chip.spec.higherIsBetter === false ? delta <= 0 : delta >= 0
                      return (
                        <tr key={d.id} className={idx % 2 === 0 ? 'hover:bg-gray-50/50' : 'bg-gray-50/30 hover:bg-gray-50'}>
                          <td className="px-4 py-2.5 font-medium text-gray-800">{d.name}</td>
                          {tableYears.map((y) => (
                            <td
                              key={y}
                              className={`px-4 py-2.5 text-right tabular-nums ${y === year ? 'font-semibold text-primary-700' : 'text-gray-600'}`}
                            >
                              {s.has(y) ? formatNumber(scaled(s.get(y)!), chip.digits) : '—'}
                            </td>
                          ))}
                          <td className={`px-4 py-2.5 text-right font-medium ${good === null ? 'text-gray-400' : good ? 'text-green-600' : 'text-red-600'}`}>
                            {delta === null ? '—' : `${delta >= 0.05 ? '+' : ''}${formatNumber(Math.abs(delta) < 0.05 ? 0 : delta)}${chip.rate ? (chip.unit === '%' ? ' п.п.' : '') : '%'}`}
                          </td>
                        </tr>
                      )
                    })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </>
  )
}

function CompareChart({ series, years, scale, colors, unit }: {
  series: { id: string; name: string; series: YearSeries }[]
  years: number[]
  scale: number
  colors: string[]
  unit: string
}) {
  const W = 1240
  const H = 340
  const PAD_R = 18
  const PAD_T = 14
  const PAD_B = 30
  const vals = series.flatMap((s) => years.map((y) => s.series.get(y)).filter((v): v is number => v !== undefined)).map((v) => v / scale)
  if (vals.length === 0) return <p className="py-8 text-center text-base text-gray-400">нет данных</p>
  const axis = niceScale(Math.min(...vals), Math.max(...vals))
  const PAD_L = yAxisPad(axis, false)
  const missing = years.filter((yr) => series.every((s) => !s.series.has(yr)))
  const x = (i: number) => PAD_L + (i / Math.max(1, years.length - 1)) * (W - PAD_L - PAD_R)
  const y = (v: number) => PAD_T + ((axis.max - v) / (axis.max - axis.min)) * (H - PAD_T - PAD_B)
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        <YAxis scale={axis} x={PAD_L} top={PAD_T} height={H - PAD_T - PAD_B} gridTo={W - PAD_R} />
        {series.map((s) => {
          const col = COMPARE_COLORS[colors.indexOf(s.id)]
          const pts = years
            .map((yr, i) => ({ i, v: s.series.get(yr) }))
            .filter((p): p is { i: number; v: number } => p.v !== undefined)
          const d = pts.map((p, k) => `${k ? 'L' : 'M'}${x(p.i)},${y(p.v / scale)}`).join(' ')
          return (
            <g key={s.id}>
              <path d={d} fill="none" stroke={col} strokeWidth="2" strokeLinejoin="round" />
              {pts.map((p) => (
                <circle key={p.i} cx={x(p.i)} cy={y(p.v / scale)} r={3} fill="white" stroke={col} strokeWidth="1.5" />
              ))}
            </g>
          )
        })}
        {years.map((yr, i) => (
          <text key={yr} x={x(i)} y={H - 8} textAnchor="middle" fontSize={AXIS_FONT} fill="#6b7280">
            {yr}
          </text>
        ))}
      </svg>
      {missing.length > 0 && (
        <p className="mt-1 text-right text-sm text-gray-400">
          {missing.join(', ')}: данные не опубликованы
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-3">
        {series.map((s) => (
          <div key={s.id} className="flex items-center gap-1.5 text-sm text-gray-600">
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: COMPARE_COLORS[colors.indexOf(s.id)] }} />
            {s.name}
          </div>
        ))}
      </div>
    </div>
  )
}
