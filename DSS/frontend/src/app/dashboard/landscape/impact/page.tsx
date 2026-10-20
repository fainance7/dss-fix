'use client'

/**
 * Ландшафтное планирование → оценка социально-экономического эффекта (ЗБ-УР):
 * влияние земельного баланса на устойчивое развитие. Подстраница основного
 * экрана /dashboard/landscape (карта вариантов зонирования V1/V2/V3).
 *
 * Экран алгоритма ЗБ-УР (docs/Алгоритм_ЗБ_УР_2026-09-09.md). Отвечает на вопрос
 * заказчика: как изменение земельного баланса влияет на статистику области и
 * через неё на индексы устойчивого развития, и какой из вариантов выбрать.
 *
 * Правила отображения, обязательные к соблюдению:
 *   • точечная оценка без интервала на экран не выводится нигде;
 *   • рядом с каждым числом стоит метка типа связи [Б] / [С] / [Э];
 *   • слово «прогноз» применительно к абсолютным значениям здесь не используется:
 *     модуль считает контрфактическую разницу с базовым сценарием (инерцией);
 *   • статус гейтов и перечень непройденных проверок видны всегда.
 */

import Link from 'next/link'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useAuthStore } from '@/store/authStore'
import {
  LAND_CATEGORIES,
  LAND_CATEGORY_LABELS,
  compareLandscapeRuns,
  createLandscapeRun,
  estimateLandscapeImpact,
  fetchLandscapeVariantPresets,
  fetchLandscapeAssumptions,
  fetchLandscapeCalibration,
  fetchLandscapeRun,
  fetchLandscapeRuns,
  type LandUseShift,
  type LandscapeVariantPresets,
} from '@/lib/api/ai'
import type {
  AssumptionItem,
  Band,
  DistributionSummary,
  ExpressEstimate,
  LandCategory,
  LandscapeAssumptions,
  LandscapeCalibration,
  LandscapeRun,
} from '@/lib/api/ai'

type Tab = 'calculator' | 'runs' | 'calibration' | 'assumptions'

/** Варианты ландшафтного планирования. Сдвиг угодий по умолчанию приходит с
 *  сервера (/landscape-impact/variant-presets): он посчитан наложением растров
 *  V1–V3 на маски категорий земель. Изменения категорий земель — ручные, по
 *  умолчанию нулевые: зонирование меняет использование земель, а не их категорию. */
const VARIANT_PRESETS: Array<{ code: string; label: string; hint: string }> = [
  {
    code: 'lp-v1-conservative',
    label: 'V1 — природоохранный',
    hint: 'Часть полей выводится в регулируемое использование и пастбища.',
  },
  {
    code: 'lp-v2-balanced',
    label: 'V2 — сбалансированный',
    hint: 'Распашка части пастбищ и залежей при сохранении экологических ограничений.',
  },
  {
    code: 'lp-v3-development',
    label: 'V3 — хозяйственно-ориентированный',
    hint: 'Максимальная распашка земель, пригодных для пашенного земледелия.',
  },
]

const ZERO_SHIFT: LandUseShift = { arable: 0, pasture: 0 }

function tha(value: number): string {
  return value.toLocaleString('ru-RU', { maximumFractionDigits: 0 })
}

const BASE_YEAR = 2024

const VERDICT_STYLE: Record<string, { bg: string; text: string; ring: string }> = {
  supports: { bg: 'bg-emerald-50', text: 'text-emerald-800', ring: 'ring-emerald-200' },
  hinders: { bg: 'bg-rose-50', text: 'text-rose-800', ring: 'ring-rose-200' },
  inconclusive: { bg: 'bg-amber-50', text: 'text-amber-800', ring: 'ring-amber-200' },
}

const GATE_STATUS_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  PUBLISH: { bg: 'bg-emerald-50', text: 'text-emerald-700', label: 'Допущен к публикации' },
  PUBLISH_WITH_WARNING: { bg: 'bg-amber-50', text: 'text-amber-700', label: 'Публикация с оговорками' },
  RESEARCH_ONLY: { bg: 'bg-slate-100', text: 'text-slate-700', label: 'Только для исследования' },
}

const LINK_HINT: Record<string, string> = {
  'Б': 'Балансовое тождество: проверяется на непротиворечивость, данными не опровергается',
  'С': 'Статистическая оценка из данных: с интервалом, бэктестом и гейтами',
  'Э': 'Экспертное допущение: диапазон [min, base, max] с указанным источником',
}

function fmt(value: number, digits = 3): string {
  return value.toLocaleString('ru-RU', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function signed(value: number, digits = 3): string {
  return `${value >= 0 ? '+' : '−'}${fmt(Math.abs(value), digits)}`
}

/** Метка типа связи рядом с числом. Обязательный элемент интерфейса. */
function LinkBadge({ labels }: { labels: string | string[] }) {
  const list = Array.isArray(labels) ? labels : labels.split('+')
  return (
    <span className="inline-flex gap-1">
      {list.map((l) => (
        <span
          key={l}
          title={LINK_HINT[l] ?? l}
          className="inline-flex h-5 w-5 items-center justify-center rounded border border-gray-300 bg-white text-[11px] font-semibold text-gray-600"
        >
          {l}
        </span>
      ))}
    </span>
  )
}

function BandCard({ title, band, unit, digits = 2 }: { title: string; band: Band; unit?: string; digits?: number }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="mb-1 flex items-start justify-between gap-2">
        <span className="text-sm text-gray-500">{title}</span>
        <LinkBadge labels={band.link} />
      </div>
      <div className="text-2xl font-semibold text-gray-900">
        {signed(band.base, digits)}
        <span className="ml-1 text-sm font-normal text-gray-500">{unit ?? band.unit}</span>
      </div>
      <div className="mt-1 text-xs text-gray-500">
        диапазон {signed(band.low, digits)} … {signed(band.high, digits)}
      </div>
    </div>
  )
}

function DistributionCard({
  title,
  dist,
  mde,
  unit,
  digits = 4,
  labels,
}: {
  title: string
  dist: DistributionSummary
  mde?: number
  unit?: string
  digits?: number
  labels: string[]
}) {
  const belowMde = mde !== undefined && Math.abs(dist.median) < mde
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="mb-1 flex items-start justify-between gap-2">
        <span className="text-sm text-gray-500">{title}</span>
        <LinkBadge labels={labels} />
      </div>
      <div className="text-2xl font-semibold text-gray-900">
        {signed(dist.median, digits)}
        {unit && <span className="ml-1 text-sm font-normal text-gray-500">{unit}</span>}
      </div>
      <div className="mt-1 text-xs text-gray-500">
        80 % интервал {signed(dist.ci80[0], digits)} … {signed(dist.ci80[1], digits)} ·
        P(Δ&gt;0) = {fmt(dist.p_positive, 2)}
      </div>
      {mde !== undefined && (
        <div className={`mt-2 text-xs ${belowMde ? 'text-amber-700' : 'text-gray-400'}`}>
          {belowMde
            ? `меньше минимального содержательного эффекта ${fmt(mde, digits)} — неотличимо от исторического шума`
            : `минимальный содержательный эффект ${fmt(mde, digits)}`}
        </div>
      )}
    </div>
  )
}

export default function LandscapeImpactPage() {
  const { user } = useAuthStore()
  const [tab, setTab] = useState<Tab>('calculator')

  const [preset, setPreset] = useState(VARIANT_PRESETS[2].code)
  const [horizon, setHorizon] = useState(10)
  const [changes, setChanges] = useState<Partial<Record<LandCategory, number>>>({})
  const [presets, setPresets] = useState<LandscapeVariantPresets | null>(null)
  const [shift, setShift] = useState<LandUseShift>(ZERO_SHIFT)

  const [estimate, setEstimate] = useState<ExpressEstimate | null>(null)
  const [estimating, setEstimating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [runs, setRuns] = useState<LandscapeRun[]>([])
  const [activeRun, setActiveRun] = useState<LandscapeRun | null>(null)
  const [running, setRunning] = useState(false)
  const [comparison, setComparison] = useState<{ pairs: any[]; note: string } | null>(null)

  const [calibration, setCalibration] = useState<LandscapeCalibration | null>(null)
  const [assumptions, setAssumptions] = useState<LandscapeAssumptions | null>(null)

  const baseBalance = useMemo(
    () => calibration?.balance_ok ?? true,
    [calibration],
  )

  /** Целевые площади = базовый год + заданные изменения. Базу берём из прогона,
   *  если он уже есть; иначе из константы земельного баланса 2024 года. */
  const BASE_2024: Record<LandCategory, number> = {
    agricultural: 7163.1,
    settlements: 1066.3,
    industry: 66.9,
    protected: 134.9,
    forest: 545.1,
    water: 142.4,
    reserve: 685.6,
  }

  const targetAreas = useMemo(() => {
    const out: Record<string, number> = {}
    for (const cat of LAND_CATEGORIES) {
      out[cat] = Number((BASE_2024[cat] + (changes[cat] ?? 0)).toFixed(3))
    }
    return out
  }, [changes])

  const netChange = useMemo(
    () => LAND_CATEGORIES.reduce((s, c) => s + (changes[c] ?? 0), 0),
    [changes],
  )

  const selectedVariant = VARIANT_PRESETS.find((v) => v.code === preset)

  const runEstimate = useCallback(async () => {
    setEstimating(true)
    setError(null)
    try {
      const data = await estimateLandscapeImpact({
        name: selectedVariant?.label ?? 'Пользовательский вариант',
        variant_code: preset,
        target_areas: targetAreas,
        land_use_shift: shift,
        horizon_years: horizon,
        base_year: BASE_YEAR,
      })
      setEstimate(data)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Не удалось выполнить экспресс-оценку')
    } finally {
      setEstimating(false)
    }
  }, [preset, targetAreas, shift, horizon, selectedVariant])

  const loadRuns = useCallback(async () => {
    try {
      setRuns(await fetchLandscapeRuns(30))
    } catch {
      /* список прогонов не критичен для работы экрана */
    }
  }, [])

  const startFullRun = useCallback(async () => {
    setRunning(true)
    setError(null)
    try {
      const created = await createLandscapeRun({
        name: `${selectedVariant?.label ?? 'Вариант'} · горизонт ${horizon} лет`,
        variant_code: preset,
        target_areas: targetAreas,
        land_use_shift: shift,
        horizon_years: horizon,
        base_year: BASE_YEAR,
        draws: 5000,
      })
      setTab('runs')
      // Полный прогон идёт фоном; опрашиваем до завершения.
      let attempt = 0
      const poll = async () => {
        attempt += 1
        const fresh = await fetchLandscapeRun(created.id)
        setActiveRun(fresh)
        await loadRuns()
        if (fresh.status === 'pending' || fresh.status === 'running') {
          // Полный Монте-Карло (5000 прогонов) идёт 7–8 минут: опрашиваем до
          // 20 минут, первые 30 с чаще — короткие прогоны видно сразу.
          if (attempt < 250) setTimeout(poll, attempt < 20 ? 1500 : 5000)
          else setRunning(false)
        } else {
          setRunning(false)
        }
      }
      void poll()
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Не удалось запустить полный расчёт')
      setRunning(false)
    }
  }, [preset, targetAreas, shift, horizon, selectedVariant, loadRuns])

  useEffect(() => {
    void loadRuns()
    fetchLandscapeCalibration().then(setCalibration).catch(() => undefined)
    fetchLandscapeAssumptions().then(setAssumptions).catch(() => undefined)
    fetchLandscapeVariantPresets()
      .then((p) => {
        setPresets(p)
        const v = p.variants[VARIANT_PRESETS[2].code]
        if (v) setShift({ arable: v.arable, pasture: v.pasture })
      })
      .catch(() => undefined)
  }, [loadRuns])

  const presetShift = presets?.variants[preset]

  useEffect(() => {
    void runEstimate()
    // Пересчитываем экспресс-оценку при каждом изменении варианта или горизонта.
  }, [runEstimate])

  const compare = useCallback(async () => {
    const completed = runs.filter((r) => r.status === 'completed').slice(0, 3)
    if (completed.length < 2) return
    try {
      setComparison(await compareLandscapeRuns(completed.map((r) => r.id)))
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Не удалось сравнить прогоны')
    }
  }, [runs])

  return (
    <div className="space-y-6 px-4 py-6">
      <header>
        <Link href="/dashboard/landscape" className="text-sm text-primary-700 hover:underline">
          ← Ландшафтное планирование
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold text-gray-900">
            Оценка социально-экономического эффекта (ЗБ-УР)
          </h1>
          {assumptions && (
            <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-600">
              алгоритм ЗБ-УР v{assumptions.algorithm_version}
            </span>
          )}
        </div>
        <p className="mt-2 max-w-4xl text-sm text-gray-600">
          Оценка влияния земельного баланса на устойчивое развитие области. Алгоритм откалиброван
          на историческом окне 2014–2024 и применяется к вариантам землепользования. Модуль
          считает <b>контрфактическую разницу с базовым сценарием (инерцией)</b>, а не прогноз
          абсолютных значений: одиннадцать годовых наблюдений такого прогноза не выдерживают.
        </p>
      </header>

      <nav className="flex flex-wrap gap-1 border-b border-gray-200">
        {([
          ['calculator', 'Оценка варианта'],
          ['runs', 'Полные прогоны'],
          ['calibration', 'Калибровка 2014–2024'],
          ['assumptions', 'Допущения'],
        ] as Array<[Tab, string]>).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${
              tab === id
                ? 'border-primary-600 text-primary-700'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </div>
      )}

      {tab === 'calculator' && (
        <section className="grid gap-6 lg:grid-cols-[minmax(0,380px)_1fr]">
          <div className="space-y-4">
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <h2 className="mb-3 text-sm font-semibold text-gray-900">Вариант землепользования</h2>
              <div className="space-y-2">
                {VARIANT_PRESETS.map((v) => (
                  <label
                    key={v.code}
                    className={`block cursor-pointer rounded-lg border p-3 transition ${
                      preset === v.code
                        ? 'border-primary-400 bg-primary-50'
                        : 'border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <input
                        type="radio"
                        name="variant"
                        checked={preset === v.code}
                        onChange={() => {
                          setPreset(v.code)
                          setChanges({})
                          const p = presets?.variants[v.code]
                          setShift(p ? { arable: p.arable, pasture: p.pasture } : ZERO_SHIFT)
                        }}
                      />
                      <span className="text-sm font-medium text-gray-900">{v.label}</span>
                    </div>
                    <p className="mt-1 pl-6 text-xs text-gray-500">{v.hint}</p>
                  </label>
                ))}
              </div>
              <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                Классы вариантов V1/V2/V3 — функциональные зоны, а не категории земель.
                Вариант переводится в сдвиг угодий наложением его карты на маски
                категорий земель; категории земель по умолчанию не меняются.
              </p>
            </div>

            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <div className="mb-3 flex items-start justify-between gap-2">
                <h2 className="text-sm font-semibold text-gray-900">
                  Сдвиг угодий внутри земель с/х назначения, тыс. га
                </h2>
                <LinkBadge labels="Э" />
              </div>
              {([
                ['arable', 'Пашня'],
                ['pasture', 'Пастбища'],
              ] as Array<[keyof LandUseShift, string]>).map(([key, label]) => (
                <div key={key} className="mb-2 flex items-center gap-2">
                  <span className="flex-1 text-xs text-gray-600">{label}</span>
                  <input
                    type="number"
                    step="10"
                    value={shift[key]}
                    onChange={(e) => setShift({ ...shift, [key]: Number(e.target.value) })}
                    className="w-24 rounded border border-gray-300 px-2 py-1 text-right text-sm"
                  />
                </div>
              ))}
              {presetShift && (
                <div className="mt-3 space-y-1 rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600">
                  <div className="font-medium text-gray-800">По карте варианта (полная реализация):</div>
                  <div>поля → регулируемое использование: −{tha(presetShift.components.fields_to_regulated)}</div>
                  <div>поля → пастбища: −{tha(presetShift.components.fields_to_pasture)}</div>
                  <div>
                    распашка прочих земель (пашня и орошение): +
                    {tha(presetShift.components.other_to_arable + presetShift.components.other_to_irrigated)}
                  </div>
                  <div className="text-gray-400">
                    без зонирования: {tha(presetShift.components.fields_unzoned)} полей и{' '}
                    {tha(presetShift.components.other_unzoned)} прочих земель
                  </div>
                </div>
              )}
              {presets && (
                <p className="mt-2 text-[11px] leading-snug text-gray-500">{presets.rule}</p>
              )}
            </div>

            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <h2 className="mb-3 text-sm font-semibold text-gray-900">
                Изменение площадей, тыс. га
              </h2>
              <div className="space-y-2">
                {LAND_CATEGORIES.map((cat) => (
                  <div key={cat} className="flex items-center gap-2">
                    <span className="flex-1 text-xs text-gray-600">
                      {LAND_CATEGORY_LABELS[cat]}
                    </span>
                    <input
                      type="number"
                      step="10"
                      value={changes[cat] ?? 0}
                      onChange={(e) =>
                        setChanges({ ...changes, [cat]: Number(e.target.value) })
                      }
                      className="w-24 rounded border border-gray-300 px-2 py-1 text-right text-sm"
                    />
                  </div>
                ))}
              </div>
              <div
                className={`mt-3 rounded-lg px-3 py-2 text-xs ${
                  Math.abs(netChange) < 0.01
                    ? 'bg-emerald-50 text-emerald-800'
                    : 'bg-amber-50 text-amber-800'
                }`}
              >
                Сумма изменений: {signed(netChange, 1)} тыс. га.{' '}
                {Math.abs(netChange) < 0.01
                  ? 'Баланс сходится: каждый гектар имеет источник.'
                  : 'Земельный баланс области постоянен (9 804,3 тыс. га), поэтому невязка будет распределена по всем категориям пропорционально площади.'}
              </div>
            </div>

            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <label className="mb-2 block text-sm font-semibold text-gray-900">
                Горизонт, лет: {horizon}
              </label>
              <input
                type="range"
                min={1}
                max={22}
                value={horizon}
                onChange={(e) => setHorizon(Number(e.target.value))}
                className="w-full"
              />
              <p className="mt-2 text-xs text-gray-500">
                Расчёты дальше удвоенной длины окна данных (22 года) не выпускаются.
                График внедрения варианта заказчиком не передан — принято линейное
                внедрение за 10 лет и помечено как допущение разработчика.
              </p>
            </div>

            <button
              onClick={startFullRun}
              disabled={running}
              className="w-full rounded-lg bg-primary-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-primary-700 disabled:opacity-50"
            >
              {running ? 'Идёт полный расчёт…' : 'Запустить полный расчёт (Монте-Карло)'}
            </button>
          </div>

          <div className="space-y-4">
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <h2 className="text-sm font-semibold text-gray-900">
                Экспресс-оценка влияния на рост экономики
              </h2>
              <p className="mt-1 text-xs text-gray-500">
                Аналитическая свёртка диапазонов допущений. Вероятности и вердикт по
                устойчивому развитию даёт полный расчёт.
              </p>
            </div>

            {estimating && !estimate && (
              <div className="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500">
                Считаем…
              </div>
            )}

            {estimate && (
              <>
                <div className="grid gap-3 sm:grid-cols-2">
                  <BandCard
                    title="Валовая продукция сельского хозяйства"
                    band={estimate.agri_output}
                    unit="млрд ₸"
                  />
                  <BandCard
                    title="Валовая продукция СХ, относительно"
                    band={estimate.agri_output_rel}
                    unit="доля"
                    digits={4}
                  />
                  <BandCard title="Валовой региональный продукт" band={estimate.grp} unit="млрд ₸" />
                  <BandCard
                    title="ВРП, относительно"
                    band={estimate.grp_rel}
                    unit="доля"
                    digits={4}
                  />
                </div>

                {estimate.land_use_shift_check?.note && (
                  <div
                    className={`rounded-lg px-4 py-3 text-xs ${
                      estimate.land_use_shift_check.exceeds_history
                        ? 'bg-amber-50 text-amber-800'
                        : 'bg-gray-50 text-gray-700'
                    }`}
                  >
                    {estimate.land_use_shift_check.note} Реализовано к горизонту:{' '}
                    {Math.round(estimate.land_use_shift_check.realized_fraction * 100)} %.
                  </div>
                )}

                {estimate.district_check && estimate.district_check.total > 0 && (
                  <div
                    className={`rounded-lg px-4 py-3 text-xs ${
                      estimate.district_check.outside.length > 0
                        ? 'bg-amber-50 text-amber-800'
                        : 'bg-gray-50 text-gray-700'
                    }`}
                  >
                    Сдвиг пашни в пределах наблюдавшегося размаха посевов района (× 1,5):{' '}
                    {estimate.district_check.inside_count} из {estimate.district_check.total} районов.
                    {estimate.district_check.outside.length > 0 && (
                      <>
                        {' '}Вне размаха:{' '}
                        {estimate.district_check.outside
                          .map((d) => `${d.region} (${d.arable > 0 ? '+' : ''}${d.arable.toFixed(0)} тыс. га при пределе ${d.limit.toFixed(0)})`)
                          .join('; ')}
                        . По этим районам оценка — экстраполяция: она показывает масштаб, а не измеренный эффект.
                      </>
                    )}
                  </div>
                )}

                {estimate.transition_violations.length > 0 && (
                  <div className="space-y-2">
                    {estimate.transition_violations.map((v, i) => (
                      <div
                        key={i}
                        className={`rounded-lg px-4 py-3 text-xs ${
                          v.severity === 'blocking'
                            ? 'bg-rose-50 text-rose-800'
                            : 'bg-amber-50 text-amber-800'
                        }`}
                      >
                        {v.message}
                      </div>
                    ))}
                  </div>
                )}

                <div className="rounded-xl border border-gray-200 bg-white p-4">
                  <h3 className="mb-2 text-sm font-semibold text-gray-900">
                    Земельный баланс: откуда каждый гектар
                  </h3>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-gray-500">
                        <th className="pb-2">Категория</th>
                        <th className="pb-2 text-right">2024, тыс. га</th>
                        <th className="pb-2 text-right">Изменение</th>
                      </tr>
                    </thead>
                    <tbody>
                      {LAND_CATEGORIES.map((cat) => {
                        const delta = estimate.land_balance_delta[cat] ?? 0
                        return (
                          <tr key={cat} className="border-t border-gray-100">
                            <td className="py-1.5 text-gray-700">{LAND_CATEGORY_LABELS[cat]}</td>
                            <td className="py-1.5 text-right tabular-nums text-gray-500">
                              {fmt(BASE_2024[cat], 1)}
                            </td>
                            <td
                              className={`py-1.5 text-right tabular-nums font-medium ${
                                Math.abs(delta) < 0.01
                                  ? 'text-gray-400'
                                  : delta > 0
                                    ? 'text-emerald-700'
                                    : 'text-rose-700'
                              }`}
                            >
                              {Math.abs(delta) < 0.01 ? '—' : signed(delta, 1)}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>

                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 text-xs text-gray-600">
                  {estimate.disclaimer}
                  <div className="mt-2">
                    Без подписи экспертов заказчика: {estimate.unverified_assumptions.length} из{' '}
                    {assumptions?.assumptions.length ?? '—'} коэффициентов.{' '}
                    <button
                      onClick={() => setTab('assumptions')}
                      className="font-medium text-primary-700 underline"
                    >
                      Посмотреть допущения
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </section>
      )}

      {tab === 'runs' && (
        <section className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={compare}
              disabled={runs.filter((r) => r.status === 'completed').length < 2}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 transition hover:bg-gray-50 disabled:opacity-40"
            >
              Сравнить последние прогоны
            </button>
            <span className="text-xs text-gray-500">
              Модуль не строит рейтинга вариантов: выбор — управленческое решение.
            </span>
          </div>

          {comparison && (
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <h3 className="mb-2 text-sm font-semibold text-gray-900">Попарное сравнение</h3>
              <ul className="space-y-2 text-sm text-gray-700">
                {comparison.pairs.map((p, i) => (
                  <li key={i} className="rounded-lg bg-gray-50 px-3 py-2">
                    {p.conclusion}
                    {p.caveat && (
                      <div className="mt-1 text-xs text-amber-700">{p.caveat}</div>
                    )}
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-gray-500">{comparison.note}</p>
            </div>
          )}

          <div className="grid gap-4 lg:grid-cols-[minmax(0,320px)_1fr]">
            <div className="rounded-xl border border-gray-200 bg-white">
              <div className="border-b border-gray-100 px-4 py-3 text-sm font-semibold text-gray-900">
                Прогоны
              </div>
              <ul className="max-h-[70vh] divide-y divide-gray-100 overflow-y-auto">
                {runs.length === 0 && (
                  <li className="px-4 py-6 text-center text-sm text-gray-400">
                    Прогонов ещё нет
                  </li>
                )}
                {runs.map((r) => (
                  <li key={r.id}>
                    <button
                      onClick={() => fetchLandscapeRun(r.id).then(setActiveRun)}
                      className={`w-full px-4 py-3 text-left transition hover:bg-gray-50 ${
                        activeRun?.id === r.id ? 'bg-primary-50' : ''
                      }`}
                    >
                      <div className="text-sm font-medium text-gray-900">{r.name}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                        <span className="text-gray-500">
                          {new Date(r.created_at).toLocaleString('ru-RU')}
                        </span>
                        {r.gate_status && (
                          <span
                            className={`rounded px-1.5 py-0.5 ${
                              GATE_STATUS_STYLE[r.gate_status]?.bg ?? 'bg-gray-100'
                            } ${GATE_STATUS_STYLE[r.gate_status]?.text ?? 'text-gray-600'}`}
                          >
                            {GATE_STATUS_STYLE[r.gate_status]?.label ?? r.gate_status}
                          </span>
                        )}
                        {r.status !== 'completed' && (
                          <span className="text-amber-600">{r.status}</span>
                        )}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            <div className="space-y-4">
              {!activeRun && (
                <div className="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-400">
                  Выберите прогон слева или запустите новый на вкладке «Оценка варианта»
                </div>
              )}

              {activeRun?.error && (
                <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
                  {activeRun.error}
                </div>
              )}

              {activeRun?.result && (
                <>
                  <div
                    className={`rounded-xl p-5 ring-1 ${
                      VERDICT_STYLE[activeRun.result.verdict.code].bg
                    } ${VERDICT_STYLE[activeRun.result.verdict.code].ring}`}
                  >
                    <div
                      className={`text-xl font-semibold ${
                        VERDICT_STYLE[activeRun.result.verdict.code].text
                      }`}
                    >
                      {activeRun.result.verdict.label}
                    </div>
                    <p className="mt-2 text-sm text-gray-700">
                      {activeRun.result.verdict.reason}
                    </p>
                    <p className="mt-2 text-sm text-gray-600">
                      {activeRun.result.verdict.statement}
                    </p>
                    {activeRun.result.verdict.eco_veto && (
                      <p className="mt-2 rounded bg-white/60 px-3 py-2 text-sm text-rose-800">
                        Сработало вето по экологическому блоку: улучшение экономики достигается
                        ценой экологической составляющей.
                      </p>
                    )}
                  </div>

                  <div>
                    <h3 className="mb-1 text-sm font-semibold text-gray-900">
                      Индекс DSS — сравнение вариантов землепользования
                    </h3>
                    <p className="mb-3 text-xs text-gray-500">
                      {activeRun.result.sd_index.index_roles.dss}
                    </p>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    {(
                      [
                        ['dss_total', 'Индекс УР, итог'],
                        ['dss_social', 'Социальный блок'],
                        ['dss_economic', 'Экономический блок'],
                        ['dss_ecological', 'Экологический блок'],
                      ] as Array<[string, string]>
                    ).map(([key, title]) =>
                      activeRun.result!.sd_index.deltas[key] ? (
                        <DistributionCard
                          key={key}
                          title={title}
                          dist={activeRun.result!.sd_index.deltas[key]}
                          mde={key === 'dss_total' ? activeRun.result!.sd_index.mde : undefined}
                          labels={['Э']}
                        />
                      ) : null,
                    )}
                  </div>

                  {activeRun.result.sd_index.deltas.official_total && (
                    <>
                      <div>
                        <h3 className="mb-1 text-sm font-semibold text-gray-900">
                          Официальный индекс — мониторинг по методике заказчика
                        </h3>
                        <p className="mb-3 text-xs text-gray-500">
                          {activeRun.result.sd_index.index_roles.official}
                        </p>
                      </div>
                      <div className="grid gap-3 sm:grid-cols-2">
                        {(
                          [
                            ['official_total', 'Официальный индекс, итог', 'total'],
                            ['official_economic', 'Экономический блок', 'economic'],
                          ] as Array<[string, string, string]>
                        ).map(([key, title, mdeKey]) => (
                          <DistributionCard
                            key={key}
                            title={title}
                            dist={activeRun.result!.sd_index.deltas[key]}
                            mde={activeRun.result!.sd_index.official_mde?.[mdeKey]}
                            labels={['Э']}
                          />
                        ))}
                      </div>
                    </>
                  )}

                  <div className="grid gap-3 sm:grid-cols-2">
                    <DistributionCard
                      title="Валовая продукция СХ, млрд ₸"
                      dist={activeRun.result.economy.agri_output_abs}
                      digits={2}
                      labels={['Б', 'Э']}
                    />
                    <DistributionCard
                      title="ВРП, млрд ₸"
                      dist={activeRun.result.economy.grp_abs}
                      digits={2}
                      labels={['Э']}
                    />
                  </div>

                  <div className="rounded-xl border border-gray-200 bg-white p-4">
                    <h3 className="mb-3 text-sm font-semibold text-gray-900">
                      Достоверность: гейты валидации
                    </h3>
                    <div className="mb-3 text-xs text-gray-600">
                      Статус прогона:{' '}
                      <b>
                        {GATE_STATUS_STYLE[activeRun.result.gates.status]?.label ??
                          activeRun.result.gates.status}
                      </b>
                    </div>
                    <ul className="space-y-1.5">
                      {activeRun.result.gates.gates.map((g, i) => (
                        <li key={`${g.code}-${i}`} className="flex gap-2 text-xs">
                          <span
                            className={`mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded ${
                              g.passed
                                ? 'bg-emerald-100 text-emerald-700'
                                : g.blocking
                                  ? 'bg-rose-100 text-rose-700'
                                  : 'bg-amber-100 text-amber-700'
                            }`}
                          >
                            {g.passed ? '✓' : '!'}
                          </span>
                          <span>
                            <b className="text-gray-800">
                              {g.code} · {g.title}
                            </b>
                            {g.blocking && !g.passed && (
                              <span className="ml-1 text-rose-600">(блокирующий)</span>
                            )}
                            <span className="ml-1 text-gray-600">{g.detail}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="rounded-xl border border-gray-200 bg-white p-4">
                    <h3 className="mb-3 text-sm font-semibold text-gray-900">
                      Трассировка: чем получено каждое число
                    </h3>
                    <ul className="space-y-2">
                      {activeRun.result.trace.map((t, i) => (
                        <li key={i} className="flex gap-3 text-xs">
                          <LinkBadge labels={t.labels} />
                          <span>
                            <b className="text-gray-800">{t.quantity}</b>
                            <span className="ml-1 text-gray-500">
                              ({t.chain.join(' → ')})
                            </span>
                            <div className="mt-0.5 text-gray-600">{t.note}</div>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="rounded-xl border border-gray-200 bg-white p-4">
                    <h3 className="mb-3 text-sm font-semibold text-gray-900">
                      Вклад допущений в разброс итогового индекса
                    </h3>
                    <ul className="space-y-1.5">
                      {Object.entries(activeRun.result.sensitivity)
                        .sort(([, a], [, b]) => b - a)
                        .slice(0, 6)
                        .map(([key, share]) => (
                          <li key={key} className="flex items-center gap-2 text-xs">
                            <span className="w-56 shrink-0 truncate text-gray-700">{key}</span>
                            <span className="h-2 flex-1 overflow-hidden rounded bg-gray-100">
                              <span
                                className="block h-full bg-primary-400"
                                style={{ width: `${Math.round(share * 100)}%` }}
                              />
                            </span>
                            <span className="w-12 text-right tabular-nums text-gray-600">
                              {Math.round(share * 100)} %
                            </span>
                          </li>
                        ))}
                    </ul>
                  </div>

                  <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 text-xs text-gray-500">
                    Версии: алгоритм {activeRun.result.versions.algorithm} · витрина{' '}
                    {activeRun.result.versions.datamart} · коэффициенты{' '}
                    {activeRun.result.versions.assumptions} · seed{' '}
                    {activeRun.result.versions.seed} · реализаций{' '}
                    {activeRun.result.versions.draws}. Повторный прогон с тем же входом и seed
                    даёт тот же результат.
                  </div>
                </>
              )}
            </div>
          </div>
        </section>
      )}

      {tab === 'calibration' && calibration && (
        <section className="space-y-4">
          <div className="rounded-xl border border-gray-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-gray-900">
              Что алгоритм извлёк из истории {calibration.window[0]}–{calibration.window[1]}
            </h2>
            <p className="mt-1 text-xs text-gray-500">
              Одиннадцать годовых наблюдений — предел объёма. Ни один метод не создаёт
              информации, которой нет в данных.
            </p>
          </div>

          <div className="rounded-xl border border-gray-200 bg-white p-4">
            <h3 className="mb-2 text-sm font-semibold text-gray-900">
              Балансовое тождество{' '}
              <LinkBadge labels={['Б']} />
            </h3>
            <p className="text-sm text-gray-700">
              {baseBalance
                ? 'Сумма семи категорий земель постоянна во все годы окна и равна 9 804,3 тыс. га. Отсюда: любой вариант землепользования — игра с нулевой суммой, и прирост одной категории обязан быть покрыт убылью других.'
                : 'Балансовое тождество нарушено — прогоны не могут быть опубликованы.'}
            </p>
          </div>

          <div className="rounded-xl border border-gray-200 bg-white p-4">
            <h3 className="mb-3 text-sm font-semibold text-gray-900">
              Уравнение урожайности: связь с климатом
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-gray-500">
                    <th className="pb-2">Регрессор</th>
                    <th className="pb-2 text-right">b</th>
                    <th className="pb-2 text-right">R²</th>
                    <th className="pb-2 text-right">p</th>
                    <th className="pb-2 text-right">p с поправкой</th>
                    <th className="pb-2 text-right">MASE</th>
                    <th className="pb-2 text-center">Итог</th>
                  </tr>
                </thead>
                <tbody>
                  {calibration.yield_candidates.map((c) => (
                    <tr key={c.regressor} className="border-t border-gray-100">
                      <td className="py-1.5 text-gray-700">{c.regressor}</td>
                      <td className="py-1.5 text-right tabular-nums">{signed(c.slope)}</td>
                      <td className="py-1.5 text-right tabular-nums">{fmt(c.r2)}</td>
                      <td className="py-1.5 text-right tabular-nums">{fmt(c.p_permutation)}</td>
                      <td className="py-1.5 text-right tabular-nums">{fmt(c.p_adjusted)}</td>
                      <td className="py-1.5 text-right tabular-nums">{fmt(c.mase)}</td>
                      <td className="py-1.5 text-center">
                        <LinkBadge labels={[c.link_type]} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {calibration.notes.map((n, i) => (
              <p key={i} className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                {n}
              </p>
            ))}
          </div>

          <div className="rounded-xl border border-gray-200 bg-white p-4">
            <h3 className="mb-2 text-sm font-semibold text-gray-900">
              Прямая связь «земельный баланс → показатель»: отрицательный контроль
            </h3>
            <p className="text-sm text-gray-700">
              Проверено {calibration.direct_land_links.n_tests} пар, минимальное p ={' '}
              {fmt(calibration.direct_land_links.min_p)}, прошло поправку на множественность:{' '}
              <b>{calibration.direct_land_links.survived_fdr}</b>.
            </p>
            <p className="mt-2 text-xs text-gray-600">
              {calibration.direct_land_links.conclusion}
            </p>
          </div>

          <div className="rounded-xl border border-gray-200 bg-white p-4">
            <h3 className="mb-2 text-sm font-semibold text-gray-900">
              Минимальный содержательный эффект
            </h3>
            <div className="grid gap-2 sm:grid-cols-4">
              {(['total', 'social', 'economic', 'ecological'] as const).map((k) => (
                <div key={k} className="rounded-lg bg-gray-50 px-3 py-2">
                  <div className="text-xs text-gray-500">{k}</div>
                  <div className="text-sm font-semibold text-gray-900">
                    {fmt(calibration.mde[k], 5)}
                  </div>
                  <div className="text-[11px] text-gray-400">
                    σ = {fmt(calibration.mde[`${k}_sigma`], 5)}
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs text-gray-600">
              Эффект меньше MDE неотличим от исторического шума независимо от p-значений.
            </p>
          </div>

          {calibration.data_issues.length > 0 && (
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <h3 className="mb-2 text-sm font-semibold text-gray-900">Качество входных данных</h3>
              <ul className="space-y-2">
                {calibration.data_issues.map((issue, i) => (
                  <li
                    key={i}
                    className={`rounded-lg px-3 py-2 text-xs ${
                      issue.severity === 'warning'
                        ? 'bg-amber-50 text-amber-800'
                        : 'bg-gray-50 text-gray-600'
                    }`}
                  >
                    <b>{issue.kind}</b>
                    {issue.series ? ` · ${issue.series}` : ''}
                    {issue.year ? ` · ${issue.year}` : ''} — {issue.reason ?? issue.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {tab === 'assumptions' && assumptions && (
        <section className="space-y-4">
          <div className="rounded-xl border border-gray-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-gray-900">
              Экспертные и нормативные коэффициенты
            </h2>
            <p className="mt-1 text-xs text-gray-600">
              Скрытых констант в расчёте нет: всё, что не является балансовым тождеством или
              статистической оценкой, перечислено здесь с диапазоном и источником. Без подписи
              экспертов заказчика — {assumptions.unverified.length} из{' '}
              {assumptions.assumptions.length}.
            </p>
          </div>

          <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 text-left text-gray-500">
                <tr>
                  <th className="px-4 py-2">Коэффициент</th>
                  <th className="px-4 py-2">Значение</th>
                  <th className="px-4 py-2">Единица</th>
                  <th className="px-4 py-2">Статус</th>
                  <th className="px-4 py-2">Источник</th>
                </tr>
              </thead>
              <tbody>
                {assumptions.assumptions.map((a: AssumptionItem) => (
                  <tr key={a.key} className="border-t border-gray-100 align-top">
                    <td className="px-4 py-2">
                      <div className="font-medium text-gray-900">{a.label}</div>
                      <code className="text-[11px] text-gray-400">{a.key}</code>
                    </td>
                    <td className="px-4 py-2 tabular-nums text-gray-700">
                      {Array.isArray(a.value)
                        ? (a.value as number[]).map((v) => fmt(v, 3)).join(' / ')
                        : JSON.stringify(a.value)}
                    </td>
                    <td className="px-4 py-2 text-gray-500">{a.unit}</td>
                    <td className="px-4 py-2">
                      <span
                        className={`rounded px-1.5 py-0.5 ${
                          a.verified
                            ? 'bg-emerald-50 text-emerald-700'
                            : 'bg-amber-50 text-amber-700'
                        }`}
                      >
                        {a.verified ? 'подтверждён' : 'без подписи'}
                      </span>
                    </td>
                    <td className="max-w-md px-4 py-2 text-gray-600">{a.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="rounded-xl border border-gray-200 bg-white p-4">
            <h3 className="mb-2 text-sm font-semibold text-gray-900">
              Конструкция индекса устойчивого развития DSS
            </h3>
            <p className="text-xs text-gray-600">
              {assumptions.dss_index_definition.note}
            </p>
          </div>
        </section>
      )}
    </div>
  )
}
