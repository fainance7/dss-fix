'use client'

import { useEffect, useMemo, useState } from 'react'
import { clsx } from 'clsx'
import IndicatorView from '@/components/dashboard/IndicatorView'
import TabSummary from '@/components/dashboard/TabSummary'
import MultiLineCard, {
  type LineSeriesSpec,
} from '@/components/dashboard/MultiLineCard'
import {
  DATA_YEAR,
  fetchDirections,
  fetchIndicators,
  type Indicator,
} from '@/lib/api/backend'

/**
 * Дашборд направления «Экономическое». Структура — по ТЗ
 * (docs/dashboards/economy/Показатели дашброда.docx): 4 дашборда / 6 вкладок.
 *
 *  1. «Экономическая эффективность»
 *      • Уровень развития        — линии: индексы валовой продукции (регион / с-х / пром.)
 *      • Производительность труда — линии: индекс произв. труда; доля занятых
 *  2. «Ресурсный потенциал»       — карты: инвестиции в осн. капитал; розничная торговля
 *  3. «Результаты деятельности»   — карты: валовая продукция с-х; валовая продукция пром.
 *  4. «Продбезопасность и уровень жизни»
 *      • Продовольственная безоп. — линия: потребление продуктов; карта: прожиточный минимум
 *      • Уровень жизни            — карта: среднемес. зарплата; линия: доля населения ниже ПМ
 *
 * Карт-показатели (карта районов + динамика выбранного района + ранжирование
 * районов за год) рендерит IndicatorView; областные линейные графики — MultiLineCard.
 * Показатели резолвятся по имени из справочника направления economy (первый
 * подходящий кандидат), поэтому переименования/догрузки не ломают вёрстку.
 */

// ── спецификация панелей ─────────────────────────────────────────────

interface LinePanelSeries {
  /** Кандидаты имён показателя (подстрока, регистр игнорируется). */
  candidates: string[]
  name: string
  color: string
}

interface LinePanel {
  kind: 'line'
  title: string
  description?: string
  unit?: string
  series: LinePanelSeries[]
  yearMin?: number
  yearMax?: number
}

interface MapPanel {
  kind: 'map'
  title: string
  candidates: string[]
  unit?: string
  color: string
}

type Panel = LinePanel | MapPanel

interface TabSpec {
  id: string
  dashboard: string
  title: string
  description?: string
  panels: Panel[]
}

const TABS: TabSpec[] = [
  {
    id: 'eff-develop',
    dashboard: 'Экономическая эффективность',
    title: 'Уровень развития',
    description:
      'Индексы валовой продукции региона, сельского хозяйства и промышленности, % (с 2014 года).',
    panels: [
      {
        kind: 'line',
        title: 'Индексы валовой продукции, %',
        unit: '%',
        yearMin: 2014,
        yearMax: DATA_YEAR,
        series: [
          {
            candidates: [
              'Индекс валовой продукции региона',
              'Индекс физического объема ВРП',
            ],
            name: 'Валовая продукция региона',
            color: '#2563eb',
          },
          {
            candidates: ['Индекс валовой продукции сельского хозяйства'],
            name: 'Сельское хозяйство',
            color: '#16a34a',
          },
          {
            candidates: ['Индекс валовой продукции промышленности'],
            name: 'Промышленность',
            color: '#ea580c',
          },
        ],
      },
    ],
  },
  {
    id: 'eff-productivity',
    dashboard: 'Экономическая эффективность',
    title: 'Производительность труда',
    description:
      'Индекс производительности труда и доля занятых в экономике, % (с 2014 года).',
    panels: [
      {
        kind: 'line',
        title: 'Индекс производительности труда, %',
        unit: '%',
        yearMin: 2014,
        yearMax: DATA_YEAR,
        series: [
          {
            candidates: ['Индекс производительности труда'],
            name: 'Производительность труда',
            color: '#2563eb',
          },
        ],
      },
      {
        kind: 'line',
        title:
          'Среднегодовая доля занятых в экономике в общей численности активного населения, %',
        unit: '%',
        yearMin: 2014,
        yearMax: DATA_YEAR,
        series: [
          {
            candidates: ['Среднегодовая доля занятых'],
            name: 'Доля занятых',
            color: '#16a34a',
          },
        ],
      },
    ],
  },
  {
    id: 'resources',
    dashboard: 'Ресурсный потенциал',
    title: 'Ресурсный потенциал',
    description:
      'Объём инвестиций в основной капитал и розничной торговли по районам области.',
    panels: [
      {
        kind: 'map',
        title: 'Объём инвестиций в основной капитал',
        candidates: ['Объем инвестиций в основной капитал'],
        unit: 'млрд тенге',
        color: '#f59e0b',
      },
      {
        kind: 'map',
        title: 'Объём розничной торговли',
        candidates: ['Объем розничной торговли'],
        unit: 'млрд тенге',
        color: '#0891b2',
      },
    ],
  },
  {
    id: 'production',
    dashboard: 'Результаты деятельности',
    title: 'Результаты деятельности',
    description:
      'Валовая продукция сельского хозяйства и промышленности по районам области.',
    panels: [
      {
        kind: 'map',
        title: 'Валовая продукция сельского хозяйства',
        candidates: ['Валовая продукция сельского хозяйства'],
        unit: 'млрд тенге',
        color: '#16a34a',
      },
      {
        kind: 'map',
        title: 'Валовая продукция промышленности',
        candidates: ['Валовая продукция промышленности'],
        unit: 'млрд тенге',
        color: '#ea580c',
      },
    ],
  },
  {
    id: 'food-security',
    dashboard: 'Продбезопасность и уровень жизни',
    title: 'Продовольственная безопасность',
    description:
      'Потребление продуктов питания на душу населения и величина прожиточного минимума.',
    panels: [
      {
        kind: 'line',
        title: 'Потребление продуктов питания на душу населения, кг',
        unit: 'кг',
        yearMin: 2014,
        yearMax: DATA_YEAR,
        series: [
          {
            candidates: ['Потребление продуктов питания на душу населения'],
            name: 'Потребление продуктов питания',
            color: '#9333ea',
          },
        ],
      },
      {
        kind: 'map',
        title: 'Величина прожиточного минимума',
        candidates: ['Величина прожиточного минимума'],
        unit: 'тенге',
        color: '#2563eb',
      },
    ],
  },
  {
    id: 'living-standards',
    dashboard: 'Продбезопасность и уровень жизни',
    title: 'Уровень жизни',
    description:
      'Среднемесячная номинальная заработная плата и доля населения с доходами ниже прожиточного минимума.',
    panels: [
      {
        kind: 'map',
        title: 'Среднемесячная номинальная заработная плата',
        candidates: ['Среднемесячная номинальная заработная плата'],
        unit: 'тенге',
        color: '#9333ea',
      },
      {
        kind: 'line',
        title:
          'Доля населения, имеющие доходы ниже величины прожиточного минимума, %',
        unit: '%',
        yearMin: 2014,
        yearMax: DATA_YEAR,
        series: [
          {
            candidates: [
              'Доля населения, имеющие доходы ниже величины прожиточного',
            ],
            name: 'Доля населения ниже ПМ',
            color: '#dc2626',
          },
        ],
      },
    ],
  },
]

export default function EconomyDashboardPage() {
  const [tabId, setTabId] = useState<string>(TABS[0].id)
  const [indicators, setIndicators] = useState<Indicator[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancel = false
    setLoading(true)
    setError(null)
    fetchDirections()
      .then(async (dirs) => {
        const dir = dirs.find((d) => d.code === 'economy')
        if (!dir) {
          if (!cancel) setIndicators([])
          return
        }
        const inds = await fetchIndicators({ direction_id: dir.id, is_active: true })
        if (!cancel) setIndicators(inds)
      })
      .catch((e: unknown) => {
        if (!cancel)
          setError((e as { message?: string })?.message ?? 'Ошибка загрузки')
      })
      .finally(() => {
        if (!cancel) setLoading(false)
      })
    return () => {
      cancel = true
    }
  }, [])

  // Резолв первого показателя направления, чьё имя содержит один из кандидатов.
  const resolve = useMemo(() => {
    const byLowerName = indicators.map((i) => ({ ind: i, lc: i.name.toLowerCase() }))
    return (candidates: string[]): Indicator | null => {
      for (const c of candidates) {
        const needle = c.toLowerCase()
        const hit = byLowerName.find((x) => x.lc.includes(needle))
        if (hit) return hit.ind
      }
      return null
    }
  }, [indicators])

  const currentTab = TABS.find((t) => t.id === tabId) ?? TABS[0]

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-bold text-gray-900">Экономическое развитие</h1>
        <p className="text-base text-gray-500 mt-1">
          Северо-Казахстанская область · 4 дашборда · {TABS.length} вкладок ·
          индексы, карты районов, динамика и сравнение по году.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-base text-red-700">
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <div className="flex gap-1 -mb-px overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTabId(t.id)}
              title={t.dashboard}
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

      {loading ? (
        <div className="p-6 text-base text-gray-500">Загрузка показателей…</div>
      ) : (
        <div className="space-y-4">
          <div className="text-sm uppercase tracking-wide text-gray-400">
            Дашборд: {currentTab.dashboard}
          </div>
          {currentTab.description && (
            <p className="text-base text-gray-600">{currentTab.description}</p>
          )}
          <TabSummary
            tabId={currentTab.id}
            title={`Экономическое развитие: ${currentTab.title}`}
            directionCode="economy"
            indicatorIds={currentTab.panels.flatMap((p) =>
              p.kind === 'line'
                ? p.series.map((srs) => resolve(srs.candidates)?.id)
                : [resolve(p.candidates)?.id],
            )}
          />
          {currentTab.panels.map((panel, idx) => {
            const key = `${currentTab.id}-${idx}`
            if (panel.kind === 'line') {
              const series: LineSeriesSpec[] = panel.series.map((s) => ({
                id: resolve(s.candidates)?.id ?? null,
                name: s.name,
                color: s.color,
              }))
              return (
                <MultiLineCard
                  key={key}
                  title={panel.title}
                  description={panel.description}
                  unit={panel.unit}
                  series={series}
                  yearMin={panel.yearMin}
                  yearMax={panel.yearMax}
                />
              )
            }
            // map panel
            const ind = resolve(panel.candidates)
            if (!ind) {
              return (
                <section
                  key={key}
                  className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5"
                >
                  <h3 className="text-base font-semibold text-gray-900 mb-2">
                    {panel.title}
                  </h3>
                  <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center text-base text-gray-500">
                    Показатель не найден в справочнике направления.
                  </div>
                </section>
              )
            }
            return (
              <div key={key} className="space-y-2">
                <h3 className="text-base font-semibold text-gray-900">
                  {panel.title}
                </h3>
                <IndicatorView
                  key={ind.id}
                  indicatorId={ind.id}
                  indicatorName={ind.name}
                  unit={ind.unit_name ?? panel.unit}
                  color={panel.color}
                />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
