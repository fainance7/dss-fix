'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { fetchScenarios, fetchTasksOrNull, onlyWorkingTasks, type AiTask } from '@/lib/api/ai'
import {
  formatNumber,
  latestYear,
  normalizePopulation,
  sortedYears,
  useOverviewIndicators,
  yoy,
  type OverviewIndicator,
  type OverviewSpec,
} from '@/lib/indicators/overview'
import { AXIS_FONT, YAxis, niceScale, yAxisPad } from '@/components/dashboard/chartKit'
import { DATA_YEAR } from '@/lib/api/backend'

// Показатели обзора. «Валовой региональный продукт СХ» — по данным БНС это ВРП
// области целиком (имя в справочнике историческое, на него ссылается модуль ЗБ-УР).
const SPECS: OverviewSpec[] = [
  {
    key: 'population', direction: 'social_demog', additive: true, higherIsBetter: true,
    // Ряд на конец года, дополненный рядом на начало года (01.01.2026 → конец 2025).
    candidates: ['Численность населения на конец года', 'Численность населения (всего)'],
    startOfYear: ['Численность населения (всего)'],
    exact: true, normalize: normalizePopulation,
  },
  { key: 'grp', direction: 'agriculture', candidates: ['Валовой региональный продукт'], higherIsBetter: true },
  { key: 'grpIndex', direction: 'economy', candidates: ['Индекс физического объема ВРП'] },
  { key: 'unemployment', direction: 'labor', candidates: ['Уровень безработицы'], higherIsBetter: false },
  {
    key: 'investment', direction: 'economy', additive: true, higherIsBetter: true,
    candidates: ['Объем инвестиций в основной капитал'],
  },
]

// Ключевые показатели остальных направлений (ТЗ ФFE.2: KPI по всем направлениям).
// Водные ресурсы и климат на сводную панель не выносятся: по области у них нет
// годовых рядов (климат считается по метеостанциям, водные данные — по объектам).
interface DirectionKpi {
  spec: OverviewSpec
  label: string
  unit: string
  href: string
  digits?: number
}

const DIRECTION_KPIS: DirectionKpi[] = [
  { spec: { key: 'grainYield', direction: 'agriculture', candidates: ['Урожайность зерновых культур'], exact: true, higherIsBetter: true },
    label: 'Урожайность зерновых', unit: 'ц/га', href: '/dashboard/agriculture' },
  { spec: { key: 'salary', direction: 'economy', candidates: ['Среднемесячная номинальная заработная плата'], exact: true, higherIsBetter: true },
    label: 'Средняя заработная плата', unit: 'тыс. ₸', href: '/dashboard/economy', digits: 0 },
  { spec: { key: 'naturalIncrease', direction: 'social_demog', candidates: ['Естественный прирост населения (человек)'], exact: true, higherIsBetter: true },
    label: 'Естественный прирост', unit: 'чел.', href: '/dashboard/demography', digits: 0 },
  { spec: { key: 'emissions', direction: 'ecology', candidates: ['Выбросы загрязняющих веществ (всего)'], exact: true, higherIsBetter: false },
    label: 'Выбросы загрязняющих веществ', unit: 'тыс. т', href: '/dashboard/ecology' },
  { spec: { key: 'arable', direction: 'land', candidates: ['Площадь пашни'], exact: true, higherIsBetter: true },
    label: 'Площадь пашни', unit: 'тыс. га', href: '/dashboard/land', digits: 0 },
  { spec: { key: 'tourists', direction: 'tourism', candidates: ['Численность туристов'], exact: true, higherIsBetter: true },
    label: 'Численность туристов', unit: 'тыс. чел.', href: '/dashboard/tourism' },
  { spec: { key: 'freight', direction: 'transport', candidates: ['Перевозки грузов автомобильным транспортом'], exact: true, higherIsBetter: true },
    label: 'Перевозки грузов автотранспортом', unit: 'млн т', href: '/dashboard/transport' },
]
SPECS.push(...DIRECTION_KPIS.map((k) => k.spec))

// Тенге → тыс. тенге, человек → тыс. человек — для компактной карточки.
const KPI_SCALE: Record<string, number> = { salary: 1000, tourists: 1000 }

interface KPI {
  label: string
  value: string
  unit: string
  year: number | null
  /** Подпись периода вместо «{год} г.» (напр. «на начало 2026 г.»). */
  yearLabel?: string
  trend: number | null
  trendLabel?: string
  invert?: boolean
  color: string
}

interface DistrictStat {
  id: string
  name: string
  population: number | null
  populationYear: number | null
  growth: number | null
  active: number | null
  overdue: number | null
  completed: number | null
}

function SkeletonCard() {
  return (
    <div className="rounded-xl border-t-4 border-gray-200 bg-white p-5 shadow-sm ring-1 ring-gray-100 animate-pulse">
      <div className="h-3 w-24 rounded bg-gray-200" />
      <div className="mt-3 h-7 w-32 rounded bg-gray-200" />
      <div className="mt-2 h-5 w-16 rounded-full bg-gray-200" />
    </div>
  )
}

// invert: рост — плохо (безработица), тогда снижение подсвечивается зелёным.
function TrendBadge({ value, label, invert }: { value: number; label?: string; invert?: boolean }) {
  const good = invert ? value <= 0 : value >= 0
  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-sm font-medium ${
        good ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
      }`}
    >
      {value >= 0 ? '↑' : '↓'} {formatNumber(Math.abs(value))}
      {label ?? '%'}
    </span>
  )
}

interface TooltipState {
  xPct: number
  yPct: number
  label: string
  value: number
}

function LineChart({ data, unit }: { data: { label: string; value: number | null }[]; unit: string }) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null)
  const known = data.filter((d): d is { label: string; value: number } => d.value !== null)
  if (known.length < 2) {
    return <div className="flex h-40 items-center justify-center text-base text-gray-400">нет данных</div>
  }
  const W = 600
  const H = 230
  const PAD_R = 18
  const PAD_T = 14
  const PAD_B = 30
  const scale = niceScale(Math.min(...known.map((d) => d.value)), Math.max(...known.map((d) => d.value)))
  const PAD_L = yAxisPad(scale, false)
  const baseY = H - PAD_B
  const xOf = (i: number) => PAD_L + (i / (data.length - 1)) * (W - PAD_L - PAD_R)

  const pts = data.flatMap((d, i) =>
    d.value === null
      ? []
      : [{
          x: xOf(i),
          y: PAD_T + ((scale.max - d.value) / (scale.max - scale.min)) * (H - PAD_T - PAD_B),
          label: d.label,
          value: d.value,
        }],
  )
  const missing = data.filter((d) => d.value === null).map((d) => d.label)

  const pathD = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
  const areaD = `${pathD} L${pts[pts.length - 1].x},${baseY} L${pts[0].x},${baseY} Z`
  // На длинном ряду подписываем не каждый год, чтобы подписи не слипались.
  const step = Math.ceil(data.length / 8)

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" onMouseLeave={() => setTooltip(null)}>
        <defs>
          <linearGradient id="kpiGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#6366f1" stopOpacity="0.25" />
            <stop offset="100%" stopColor="#6366f1" stopOpacity="0" />
          </linearGradient>
        </defs>
        <YAxis scale={scale} x={PAD_L} top={PAD_T} height={H - PAD_T - PAD_B} gridTo={W - PAD_R} />
        <path d={areaD} fill="url(#kpiGrad)" />
        <path d={pathD} fill="none" stroke="#6366f1" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        {pts.map((p) => (
          <circle
            key={p.label}
            cx={p.x}
            cy={p.y}
            r={4}
            fill="white"
            stroke="#6366f1"
            strokeWidth="2"
            className="cursor-pointer"
            onMouseEnter={() =>
              setTooltip({ xPct: (p.x / W) * 100, yPct: (p.y / H) * 100, label: p.label, value: p.value })
            }
          />
        ))}
        {data.map((d, i) =>
          (i % step === 0 && data.length - 1 - i >= step) || i === data.length - 1 ? (
            <text key={d.label} x={xOf(i)} y={H - 8} textAnchor="middle" fontSize={AXIS_FONT} fill="#6b7280">
              {d.label}
            </text>
          ) : null,
        )}
      </svg>
      {missing.length > 0 && (
        <p className="mt-1 text-right text-sm text-gray-400">
          {missing.join(', ')}: данные не опубликованы
        </p>
      )}
      {tooltip && (
        <div
          className="pointer-events-none absolute z-10 rounded-lg bg-gray-900 px-2.5 py-1.5 text-sm text-white shadow-lg"
          style={{ left: `${tooltip.xPct}%`, top: `${tooltip.yPct}%`, transform: 'translate(-50%, -130%)' }}
        >
          <div className="font-semibold">{tooltip.label}</div>
          <div className="text-gray-300">
            {formatNumber(tooltip.value)} {unit}
          </div>
        </div>
      )}
    </div>
  )
}

function isOverdue(t: AiTask, today: string): boolean {
  return !!t.deadline && t.deadline.slice(0, 10) < today && t.status !== 'completed' && t.status !== 'accepted' && t.status !== 'cancelled'
}

function buildKpis(data: Record<string, OverviewIndicator>): KPI[] {
  const pop = data.population
  const grp = data.grp
  const idx = data.grpIndex
  const unemp = data.unemployment
  const inv = data.investment
  const kpis: KPI[] = []

  const py = pop ? latestYear(pop.oblast) : null
  kpis.push({
    label: 'Население',
    value: py !== null ? formatNumber(pop.oblast.get(py)! / 1000) : '—',
    unit: 'тыс. чел.',
    year: py,
    // Численность — моментный показатель: конец года = начало следующего.
    yearLabel: py !== null ? `на начало ${py + 1} г.` : undefined,
    trend: py !== null ? yoy(pop.oblast, py) : null,
    color: 'border-blue-500',
  })

  // Рост ВРП — реальный (индекс физического объёма − 100) за тот же год, иначе номинальный.
  const gy = grp ? latestYear(grp.oblast) : null
  const real = gy !== null && idx ? idx.oblast.get(gy) : undefined
  kpis.push({
    label: 'ВРП области',
    value: gy !== null ? formatNumber(grp.oblast.get(gy)!, 0) : '—',
    unit: 'млрд ₸',
    year: gy,
    trend: real !== undefined ? real - 100 : gy !== null ? yoy(grp.oblast, gy) : null,
    trendLabel: real !== undefined ? '% ИФО' : '%',
    color: 'border-green-500',
  })

  const uy = unemp ? latestYear(unemp.oblast) : null
  const uPrev = uy !== null ? unemp.oblast.get(uy - 1) : undefined
  kpis.push({
    label: 'Уровень безработицы',
    value: uy !== null ? formatNumber(unemp.oblast.get(uy)!) : '—',
    unit: '%',
    year: uy,
    // Для долей — изменение в процентных пунктах, а не относительный прирост.
    trend: uy !== null && uPrev !== undefined ? unemp.oblast.get(uy)! - uPrev : null,
    trendLabel: ' п.п.',
    invert: true,
    color: 'border-yellow-500',
  })

  const iy = inv ? latestYear(inv.oblast) : null
  kpis.push({
    label: 'Инвестиции в основной капитал',
    value: iy !== null ? formatNumber(inv.oblast.get(iy)!, 0) : '—',
    unit: 'млрд ₸',
    year: iy,
    trend: iy !== null ? yoy(inv.oblast, iy) : null,
    color: 'border-purple-500',
  })
  return kpis
}

export default function DashboardPage() {
  const router = useRouter()
  const { data, regions, loading } = useOverviewIndicators(SPECS)
  const [tasks, setTasks] = useState<AiTask[] | null>(null)
  const [tasksLoading, setTasksLoading] = useState(true)
  const [drawerDistrict, setDrawerDistrict] = useState<DistrictStat | null>(null)

  useEffect(() => {
    let cancel = false
    Promise.all([fetchTasksOrNull(), fetchScenarios()]).then(([t, scenarios]) => {
      if (!cancel) {
        setTasks(t && onlyWorkingTasks(t, scenarios))
        setTasksLoading(false)
      }
    })
    return () => {
      cancel = true
    }
  }, [])

  const kpis = useMemo(() => buildKpis(data), [data])
  const directionKpis = useMemo(
    () =>
      DIRECTION_KPIS.map((k) => {
        const d = data[k.spec.key]
        const y = d ? latestYear(d.oblast) : null
        const scale = KPI_SCALE[k.spec.key] ?? 1
        return {
          ...k,
          year: y,
          value: y !== null ? formatNumber(d.oblast.get(y)! / scale, k.digits ?? 1) : '—',
          trend: y !== null ? yoy(d.oblast, y) : null,
        }
      }),
    [data],
  )

  const grpChart = useMemo(() => {
    const grp = data.grp
    if (!grp) return []
    const points: { label: string; value: number | null }[] = sortedYears(grp.oblast)
      .filter((y) => y >= 2014)
      .map((y) => ({ label: String(y), value: grp.oblast.get(y)! }))
    // Ось — до отчётного года; недостающие годы — пустые точки.
    const last = points.length ? Number(points[points.length - 1].label) : DATA_YEAR
    for (let y = last + 1; y <= DATA_YEAR; y += 1) points.push({ label: String(y), value: null })
    return points
  }, [data])

  const districts = useMemo<DistrictStat[]>(() => {
    const today = new Date().toISOString().slice(0, 10)
    const pop = data.population
    return regions
      .filter((r) => r.level === 2)
      .sort((a, b) => {
        const city = (n: string) => (n.startsWith('г.') ? 0 : 1)
        return city(a.name) - city(b.name) || a.name.localeCompare(b.name, 'ru')
      })
      .map((r) => {
        const series = pop?.districts.get(r.id)
        const y = series ? latestYear(series) : null
        const own = tasks?.filter((t) => t.region_id === r.id)
        return {
          id: r.id,
          name: r.name,
          population: y !== null ? series!.get(y)! : null,
          populationYear: y,
          growth: y !== null ? yoy(series!, y) : null,
          active: own ? own.filter((t) => t.status === 'pending' || t.status === 'in_progress').length : null,
          overdue: own ? own.filter((t) => isOverdue(t, today)).length : null,
          completed: own ? own.filter((t) => t.status === 'completed' || t.status === 'accepted').length : null,
        }
      })
  }, [data, regions, tasks])

  const grpYears = grpChart.length ? `${grpChart[0].label}–${grpChart[grpChart.length - 1].label}` : ''

  return (
    <>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Дашборд — СКО</h1>
          <p className="mt-0.5 text-base text-gray-500">
            Северо-Казахстанская область · последние опубликованные данные по каждому показателю
          </p>
        </div>
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {loading
          ? Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)
          : kpis.map((kpi) => (
              <div
                key={kpi.label}
                className={`rounded-xl border-t-4 bg-white p-5 shadow-sm ring-1 ring-gray-100 ${kpi.color}`}
              >
                <div className="text-sm font-medium uppercase tracking-wide text-gray-500">{kpi.label}</div>
                <div className="mt-2 flex items-baseline gap-1.5">
                  <span className="text-3xl font-bold text-gray-900">{kpi.value}</span>
                  {kpi.year !== null && <span className="text-base text-gray-400">{kpi.unit}</span>}
                </div>
                <div className="mt-2 flex items-center gap-2">
                  {kpi.trend !== null && (
                    <TrendBadge value={kpi.trend} label={kpi.trendLabel} invert={kpi.invert} />
                  )}
                  <span className="text-sm text-gray-400">
                    {kpi.year !== null ? kpi.yearLabel ?? `${kpi.year} г.` : 'нет данных'}
                  </span>
                </div>
              </div>
            ))}
      </div>

      <div className="mb-6 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
        <h2 className="mb-3 text-base font-semibold text-gray-900">Показатели по направлениям</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
          {directionKpis.map((k) => (
            <Link key={k.spec.key} href={k.href} className="rounded-lg bg-gray-50 p-3 hover:bg-gray-100">
              <div className="text-xs font-medium text-gray-500">{k.label}</div>
              <div className="mt-1 flex items-baseline gap-1">
                <span className="text-xl font-semibold text-gray-900">{loading ? '…' : k.value}</span>
                {k.year !== null && <span className="text-xs text-gray-400">{k.unit}</span>}
              </div>
              <div className="mt-1 flex items-center gap-1.5 text-xs text-gray-400">
                {k.trend !== null && (
                  <TrendBadge value={k.trend} invert={k.spec.higherIsBetter === false} />
                )}
                <span>{k.year !== null ? `${k.year} г.` : 'нет данных'}</span>
              </div>
            </Link>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
          <h2 className="mb-1 text-base font-semibold text-gray-900">ВРП области — динамика (млрд ₸)</h2>
          <p className="mb-4 text-sm text-gray-400">{grpYears && `${grpYears}, `}в текущих ценах</p>
          {loading ? (
            <div className="h-40 animate-pulse rounded bg-gray-100" />
          ) : (
            <LineChart data={grpChart} unit="млрд ₸" />
          )}
        </div>

        <div className="lg:col-span-2 rounded-xl bg-white shadow-sm ring-1 ring-gray-100 overflow-hidden flex flex-col">
          <div className="px-5 py-4 border-b border-gray-100">
            <h2 className="text-base font-semibold text-gray-900">Районы СКО</h2>
            {tasks === null && !tasksLoading && (
              <p className="mt-0.5 text-sm text-amber-600">Задачи недоступны: ИИ-сервис не ответил</p>
            )}
          </div>
          <div className="overflow-y-auto flex-1">
            {loading ? (
              <div className="p-4 space-y-2">
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className="h-8 rounded bg-gray-100 animate-pulse" />
                ))}
              </div>
            ) : (
              <table className="w-full text-base">
                <thead className="sticky top-0 bg-gray-50">
                  <tr>
                    <th className="px-4 py-2.5 text-left text-sm font-medium text-gray-500">Район</th>
                    <th className="px-3 py-2.5 text-right text-sm font-medium text-gray-500 whitespace-nowrap">Нас., тыс.</th>
                    <th className="px-3 py-2.5 text-right text-sm font-medium text-gray-500">Задачи</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {districts.map((d, idx) => (
                    <tr
                      key={d.id}
                      className={`cursor-pointer transition-colors hover:bg-primary-50 ${
                        idx % 2 === 0 ? '' : 'bg-gray-50/40'
                      }`}
                      onClick={() => setDrawerDistrict(d)}
                    >
                      <td className="px-4 py-2.5 font-medium text-gray-800 max-w-[200px] truncate" title={d.name}>{d.name}</td>
                      <td className="px-3 py-2.5 text-right text-gray-600">
                        {d.population !== null ? formatNumber(d.population / 1000) : '—'}
                      </td>
                      <td className="px-3 py-2.5 text-right whitespace-nowrap">
                        {d.active === null ? (
                          <span className="text-gray-400">—</span>
                        ) : d.overdue ? (
                          <span className="rounded-full bg-red-100 px-2 py-0.5 text-sm font-medium text-red-700">
                            {d.active} · ⚠ {d.overdue}
                          </span>
                        ) : d.active > 0 ? (
                          <span className="rounded-full bg-yellow-100 px-2 py-0.5 text-sm font-medium text-yellow-700">
                            {d.active}
                          </span>
                        ) : (
                          <span className="text-sm text-gray-400">0</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {drawerDistrict && (
        <>
          <div className="fixed inset-0 z-40 bg-black/20" onClick={() => setDrawerDistrict(null)} />
          <div className="fixed inset-y-0 right-0 z-50 w-80 bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-5 py-4">
              <h3 className="font-semibold text-gray-900">{drawerDistrict.name}</h3>
              <button
                onClick={() => setDrawerDistrict(null)}
                className="text-2xl leading-none text-gray-400 hover:text-gray-700"
                aria-label="Закрыть"
              >
                ×
              </button>
            </div>
            <div className="p-5 space-y-4">
              {[
                {
                  label: `Население${drawerDistrict.populationYear ? `, ${drawerDistrict.populationYear}` : ''}`,
                  value: drawerDistrict.population !== null
                    ? `${formatNumber(drawerDistrict.population, 0)} чел.`
                    : '—',
                },
                {
                  label: 'Прирост населения',
                  value: drawerDistrict.growth !== null
                    ? `${drawerDistrict.growth > 0 ? '+' : ''}${formatNumber(drawerDistrict.growth)}%`
                    : '—',
                },
                { label: 'Задач в работе', value: drawerDistrict.active !== null ? String(drawerDistrict.active) : '—' },
                { label: 'Просрочено', value: drawerDistrict.overdue !== null ? String(drawerDistrict.overdue) : '—' },
                { label: 'Выполнено', value: drawerDistrict.completed !== null ? String(drawerDistrict.completed) : '—' },
              ].map((item) => (
                <div key={item.label} className="flex items-center justify-between text-base">
                  <span className="text-gray-500">{item.label}</span>
                  <span className="font-medium text-gray-900">{item.value}</span>
                </div>
              ))}
              <div className="pt-4 border-t border-gray-100">
                <button
                  onClick={() => router.push(`/dashboard/tasks?region=${drawerDistrict.id}`)}
                  className="w-full rounded-lg bg-primary-600 px-4 py-2 text-base font-medium text-white hover:bg-primary-700 transition-colors"
                >
                  Перейти к задачам
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  )
}
