'use client'

/**
 * Ландшафтное планирование → многокритериальное сравнение вариантов V1–V3.
 *
 * Система не выбирает «лучший» вариант сама: обоснованных данных для этого нет
 * (влияние землепользования на экономику не подтверждено статистически, официальный
 * индекс УР варианты не различает, критерии и веса заказчиком не утверждены).
 * Экран показывает факты по каждому варианту (площади, тыс. га, по полному разрешению
 * растров), а веса критериев задаёт пользователь. Видно всё: исходные значения,
 * нормирование, вклад весов, доминирование и при каких весах побеждает каждый вариант.
 * Расчёт — lib/landscape/mcda.ts; данные — GET /landscape/compare.
 */

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { fetchLandscapeCompare, type CompareVariant, type LandscapeCompare } from '@/lib/api/landscape'
import {
  BLOCK_KEYS,
  SIGNIFICANCE,
  VARIANT_KEYS,
  blockShares,
  dominance,
  normalize,
  sensitivity,
  weightedScores,
  type Weights,
} from '@/lib/landscape/mcda'
import { formatNumber } from '@/components/dashboard/chartKit'

const VARIANT_COLOR: Record<CompareVariant, string> = { V1: '#16a34a', V2: '#2563eb', V3: '#ea580c' }
const MAX_WEIGHT = 24

type PresetId = 'equal' | 'nature' | 'economy' | 'feasibility'
const PRESETS: { id: PresetId; label: string; blocks: Record<string, number> }[] = [
  { id: 'equal', label: 'Равные веса блоков', blocks: { nature: 12, economy: 12, feasibility: 12 } },
  { id: 'nature', label: 'Приоритет сохранения природы', blocks: { nature: 24, economy: 12, feasibility: 12 } },
  { id: 'economy', label: 'Приоритет хозяйственного эффекта', blocks: { nature: 12, economy: 24, feasibility: 12 } },
  { id: 'feasibility', label: 'Приоритет реализуемости', blocks: { nature: 12, economy: 12, feasibility: 24 } },
]

function presetWeights(data: LandscapeCompare, id: PresetId): Weights {
  const p = PRESETS.find((x) => x.id === id) ?? PRESETS[0]
  const w: Weights = {}
  for (const b of BLOCK_KEYS) {
    const cs = data.criteria.filter((c) => c.block === b)
    for (const c of cs) w[c.id] = Math.round(p.blocks[b] / cs.length)
  }
  return w
}

const pct = (x: number) => `${formatNumber(x * 100, 0)} %`

export default function LandscapeComparePage() {
  const [data, setData] = useState<LandscapeCompare | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [territory, setTerritory] = useState<string>('oblast')
  const [weights, setWeights] = useState<Weights>({})
  const [preset, setPreset] = useState<PresetId | null>('equal')

  useEffect(() => {
    fetchLandscapeCompare()
      .then((d) => {
        setData(d)
        setWeights(presetWeights(d, 'equal'))
      })
      .catch(() => setError('Не удалось загрузить данные для сравнения вариантов'))
  }, [])

  const values = useMemo(() => {
    if (!data) return null
    if (territory === 'oblast') return data.oblast
    return data.territories.find((t) => t.kato === territory)?.values ?? data.oblast
  }, [data, territory])

  const norm = useMemo(
    () => (data && values ? data.criteria.map((c) => normalize(c, values)) : []),
    [data, values],
  )
  const scores = useMemo(() => weightedScores(norm, weights), [norm, weights])
  const dom = useMemo(
    () => (data && values ? dominance(data.criteria, values, weights) : []),
    [data, values, weights],
  )
  const sens = useMemo(
    () => (data ? sensitivity(data.criteria, norm, weights) : null),
    [data, norm, weights],
  )
  const shares = useMemo(() => (data ? blockShares(data.criteria, weights) : null), [data, weights])

  if (error) return <div className="m-6 rounded-lg bg-red-50 px-4 py-3 text-red-700">{error}</div>
  if (!data || !values || !sens || !shares) return <div className="p-6 text-gray-500">Загрузка…</div>

  const ranked = [...VARIANT_KEYS].sort((a, b) => scores[b] - scores[a])
  const leader = scores[ranked[0]] - scores[ranked[1]] > 0.5 ? ranked[0] : null
  const territoryName =
    territory === 'oblast' ? 'Северо-Казахстанская область' : data.territories.find((t) => t.kato === territory)?.name
  const changeYears = (v: CompareVariant) => values[v].land_change / data.historical_max_change_per_year

  return (
    <div className="space-y-5 px-4 py-6">
      <header className="max-w-4xl">
        <Link href="/dashboard/landscape" className="text-sm text-primary-700 hover:underline">
          ← Ландшафтное планирование
        </Link>
        <h1 className="mt-1 text-2xl font-semibold text-gray-900">Сравнение вариантов ландшафтного планирования</h1>
        <p className="mt-1.5 text-base text-gray-600">
          Система не выбирает вариант за руководителя. Она показывает, что каждый вариант предлагает сделать с
          землями области, по восьми критериям в трёх блоках. Важность критериев задаёте вы: итог, доминирование и
          чувствительность к весам пересчитываются сразу.
        </p>
      </header>

      {data.freshness.stale && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-base text-amber-800">
          Варианты ландшафтного планирования пересчитаны после подготовки критериев ({data.built_at}). Значения
          относятся к прежнему расчёту — критерии нужно обновить.
        </div>
      )}

      {/* Управление */}
      <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-gray-600">
            Территория:
            <select
              value={territory}
              onChange={(e) => setTerritory(e.target.value)}
              className="rounded border border-gray-300 px-2 py-1 text-base"
            >
              <option value="oblast">Северо-Казахстанская область</option>
              {data.territories.map((t) => (
                <option key={t.kato} value={t.kato}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                onClick={() => {
                  setWeights(presetWeights(data, p.id))
                  setPreset(p.id)
                }}
                className={`rounded-full px-3 py-1 text-sm ring-1 ${
                  preset === p.id
                    ? 'bg-primary-600 text-white ring-primary-600'
                    : 'bg-white text-gray-700 ring-gray-300 hover:bg-gray-50'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <p className="mt-3 text-sm text-gray-500">
          Веса блоков сейчас: {BLOCK_KEYS.map((b) => `${data.blocks[b].toLowerCase()} — ${pct(shares[b])}`).join('; ')}.
        </p>
      </section>

      {/* Итог */}
      <section className="grid gap-4 lg:grid-cols-3">
        {ranked.map((v, i) => (
          <div key={v} className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
            <div className="text-sm text-gray-500">{i + 1}-е место при текущих весах</div>
            <div className="mt-1 text-lg font-semibold" style={{ color: VARIANT_COLOR[v] }}>
              {data.variants[v].label}
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-3xl font-semibold text-gray-900">{formatNumber(scores[v], 0)}</span>
              <span className="text-sm text-gray-500">баллов из 100</span>
            </div>
            <div className="mt-3 h-2 rounded bg-gray-100">
              <div className="h-2 rounded" style={{ width: `${scores[v]}%`, backgroundColor: VARIANT_COLOR[v] }} />
            </div>
            <div className="mt-3 text-sm text-gray-600">
              Побеждает при {pct(sens.share[v])} возможных сочетаний весов блоков
            </div>
          </div>
        ))}
      </section>
      <div className="rounded-lg bg-gray-50 px-4 py-3 text-base text-gray-700 ring-1 ring-gray-100">
        {dom.length === 0 ? (
          <>
            <b>Нет варианта, который лучше другого по всем критериям сразу.</b> Каждый выигрывает в своём: выбор между
            ними определяется только тем, какой блок важнее. {leader
              ? `При текущих весах впереди ${data.variants[leader].label}.`
              : 'При текущих весах лидеры набирают почти одинаковый балл.'}
          </>
        ) : (
          <>
            <b>Доминирование:</b>{' '}
            {dom.map(([a, b]) => `${data.variants[a].label} не хуже ${data.variants[b].label} ни по одному критерию и лучше хотя бы по одному`).join('; ')}
            . Доминируемый вариант проигрывает при любых весах.
          </>
        )}
      </div>

      {/* Таблица критериев */}
      <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
        <h2 className="mb-1 text-base font-semibold text-gray-900">
          Критерии и исходные значения — {territoryName}, {data.unit}
        </h2>
        <p className="mb-3 text-sm text-gray-500">
          Зелёным — лучшее значение по критерию, красным — худшее. Балл критерия: лучший вариант = 1, худший = 0,
          промежуточный — пропорционально. Если варианты отличаются меньше чем на {formatNumber(SIGNIFICANCE * 100, 0)} %,
          критерий считается неразличающим.
        </p>
        <div className="overflow-x-auto">
          <table className="min-w-full border-collapse text-base">
            <thead>
              <tr className="text-left text-sm text-gray-500">
                <th className="border-b border-gray-200 py-2 pr-3 font-medium">Критерий</th>
                <th className="border-b border-gray-200 px-2 py-2 font-medium">Лучше</th>
                {VARIANT_KEYS.map((v) => (
                  <th key={v} className="border-b border-gray-200 px-2 py-2 text-right font-medium" style={{ color: VARIANT_COLOR[v] }}>
                    {v}
                  </th>
                ))}
                <th className="border-b border-gray-200 px-2 py-2 font-medium">Вес</th>
              </tr>
            </thead>
            <tbody>
              {BLOCK_KEYS.map((b) => (
                <BlockRows
                  key={b}
                  title={data.blocks[b]}
                  share={shares[b]}
                  rows={data.criteria
                    .filter((c) => c.block === b)
                    .map((c) => ({ c, n: norm.find((x) => x.id === c.id)! }))}
                  values={values}
                  weights={weights}
                  onWeight={(id, w) => {
                    setWeights((old) => ({ ...old, [id]: w }))
                    setPreset(null)
                  }}
                  extra={
                    b === 'feasibility'
                      ? VARIANT_KEYS.map((v) => `${formatNumber(changeYears(v), 1)} года`)
                      : undefined
                  }
                  extraLabel={`Лет при максимальном историческом темпе оборота земель (${formatNumber(data.historical_max_change_per_year, 1)} тыс. га в год)`}
                />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Чувствительность */}
      <section className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
          <h2 className="mb-1 text-base font-semibold text-gray-900">Какой вариант побеждает при разных весах блоков</h2>
          <p className="mb-3 text-sm text-gray-500">
            Каждая точка — сочетание весов трёх блоков (шаг 5 %), цвет — победивший вариант. Чёрный круг — текущие
            веса. Веса критериев внутри блока сохраняются.
          </p>
          <Ternary points={sens.points} current={shares} labels={data.blocks} />
          <div className="mt-2 flex flex-wrap gap-4 text-sm text-gray-600">
            {VARIANT_KEYS.map((v) => (
              <span key={v} className="inline-flex items-center gap-1.5">
                <span className="inline-block h-3 w-3 rounded-full" style={{ backgroundColor: VARIANT_COLOR[v] }} />
                {data.variants[v].label} — {pct(sens.share[v])}
              </span>
            ))}
          </div>
        </div>
        <div className="space-y-4 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
          <div>
            <h2 className="mb-2 text-base font-semibold text-gray-900">Как считается</h2>
            <ol className="list-decimal space-y-1 pl-5 text-base text-gray-700">
              <li>Для каждого варианта — площади по 8 критериям: пересечение растра варианта с картами природной значимости, уязвимости, пригодности и масками категорий земель, по полному разрешению (30,9 м).</li>
              <li>Нормирование между вариантами: лучший = 1, худший = 0.</li>
              <li>Итог = взвешенная сумма баллов, шкала 0–100. Веса задаёт пользователь.</li>
              <li>Доминирование проверяется по исходным значениям, без весов.</li>
            </ol>
          </div>
          <div>
            <h2 className="mb-2 text-base font-semibold text-gray-900">Ограничения</h2>
            <ul className="list-disc space-y-1 pl-5 text-base text-gray-700">
              {data.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </div>
          <div className="text-sm text-gray-500">
            <div className="font-medium text-gray-600">Источники</div>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">
              {data.sources.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
            <div className="mt-1">Критерии подготовлены {data.built_at}.</div>
          </div>
        </div>
      </section>
    </div>
  )
}

function BlockRows({
  title,
  share,
  rows,
  values,
  weights,
  onWeight,
  extra,
  extraLabel,
}: {
  title: string
  share: number
  rows: { c: LandscapeCompare['criteria'][number]; n: ReturnType<typeof normalize> }[]
  values: LandscapeCompare['oblast']
  weights: Weights
  onWeight: (id: string, w: number) => void
  extra?: string[]
  extraLabel: string
}) {
  return (
    <>
      <tr>
        <td colSpan={6} className="bg-gray-50 px-2 py-1.5 text-sm font-semibold text-gray-700">
          {title} — {pct(share)} веса
        </td>
      </tr>
      {rows.map(({ c, n }) => (
        <tr key={c.id} className="align-top">
          <td className="border-b border-gray-100 py-2 pr-3">
            <div className="text-gray-800">{c.label}</div>
            <div className="text-sm text-gray-400">{c.source}</div>
            {n.insignificant && <div className="text-sm text-amber-700">Варианты почти не различаются</div>}
          </td>
          <td className="border-b border-gray-100 px-2 py-2 text-sm text-gray-500 whitespace-nowrap">
            {c.better === 'lower' ? 'меньше' : 'больше'}
          </td>
          {VARIANT_KEYS.map((v) => (
            <td
              key={v}
              className={`border-b border-gray-100 px-2 py-2 text-right tabular-nums whitespace-nowrap ${
                n.best.includes(v) ? 'bg-emerald-50 font-semibold text-emerald-800' : n.worst.includes(v) ? 'bg-rose-50 text-rose-800' : ''
              }`}
              title={`Балл: ${formatNumber(n.scores[v], 2)}`}
            >
              {formatNumber(values[v][c.id] ?? 0, 1)}
            </td>
          ))}
          <td className="border-b border-gray-100 px-2 py-2">
            <div className="flex items-center gap-2">
              <input
                type="range"
                min={0}
                max={MAX_WEIGHT}
                value={weights[c.id] ?? 0}
                onChange={(e) => onWeight(c.id, Number(e.target.value))}
                className="w-28"
                aria-label={`Вес: ${c.label}`}
              />
              <span className="w-6 text-right text-sm tabular-nums text-gray-600">{weights[c.id] ?? 0}</span>
            </div>
          </td>
        </tr>
      ))}
      {extra && (
        <tr>
          <td className="border-b border-gray-100 py-2 pr-3 text-sm text-gray-500" colSpan={2}>
            {extraLabel}
          </td>
          {extra.map((x, i) => (
            <td key={i} className="border-b border-gray-100 px-2 py-2 text-right text-sm tabular-nums text-gray-600">
              {x}
            </td>
          ))}
          <td className="border-b border-gray-100" />
        </tr>
      )}
    </>
  )
}

/** Треугольная диаграмма весов трёх блоков: вершины — 100 % веса одного блока. */
function Ternary({
  points,
  current,
  labels,
}: {
  points: { w: Record<string, number>; winner: CompareVariant | null }[]
  current: Record<string, number>
  labels: Record<string, string>
}) {
  const W = 420
  const H = 380
  const top = { x: W / 2, y: 30 }
  const left = { x: 40, y: H - 40 }
  const right = { x: W - 40, y: H - 40 }
  const xy = (w: Record<string, number>) => ({
    x: w.nature * top.x + w.economy * left.x + w.feasibility * right.x,
    y: w.nature * top.y + w.economy * left.y + w.feasibility * right.y,
  })
  const cur = xy(current)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mx-auto w-full max-w-md">
      <polygon
        points={`${top.x},${top.y} ${left.x},${left.y} ${right.x},${right.y}`}
        fill="none"
        stroke="#cbd5e1"
      />
      {points.map((p, i) => {
        const { x, y } = xy(p.w)
        return <circle key={i} cx={x} cy={y} r={6} fill={p.winner ? VARIANT_COLOR[p.winner] : '#cbd5e1'} opacity={0.85} />
      })}
      <circle cx={cur.x} cy={cur.y} r={9} fill="none" stroke="#0f172a" strokeWidth={2.5} />
      <text x={top.x} y={top.y - 12} textAnchor="middle" style={{ fontSize: 14 }} fill="#334155">
        {labels.nature}
      </text>
      <text x={left.x - 10} y={left.y + 24} textAnchor="start" style={{ fontSize: 14 }} fill="#334155">
        {labels.economy}
      </text>
      <text x={right.x + 10} y={right.y + 24} textAnchor="end" style={{ fontSize: 14 }} fill="#334155">
        {labels.feasibility}
      </text>
    </svg>
  )
}
