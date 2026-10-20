'use client'

import { useEffect, useMemo, useState } from 'react'
import { clsx } from 'clsx'
import IndicatorView from '@/components/dashboard/IndicatorView'
import TabSummary from '@/components/dashboard/TabSummary'
import ClimateKpiStrip, { type ClimateKpiMetric } from '@/components/dashboard/ClimateKpiStrip'
import { WATER_COLOR_SCALES, type WaterScaleKey } from '@/lib/water/colorScales'
import { fetchDirections, fetchIndicators, type Indicator } from '@/lib/api/backend'

/**
 * Водный дашборд «Водообеспеченность территории» (docs/dashboards/water,
 * дашборды Д9 «УДВn» и Д10 «УДВ(S)»). Карта районов СКО с балльной раскраской
 * (0–5) удельной водообеспеченности по населению и по площади + динамика и
 * сравнение районов.
 *
 * Значения УДВ наполнятся через intake-portal; до этого карты показывают
 * легенду баллов и empty-state.
 */

interface MapPanel {
  title: string
  candidates: string[]
  unit?: string
  color: string
  /** Балльная шкала водообеспеченности (дискретная раскраска карты). */
  heatmap?: WaterScaleKey
}

interface TabSpec {
  id: string
  title: string
  description?: string
  panels: MapPanel[]
}

const TABS: TabSpec[] = [
  {
    id: 'population',
    title: 'По населению (УДВn)',
    description:
      'Удельная водообеспеченность населения пресными поверхностными водными ресурсами, тыс.м³/чел, и её балл (0 — катастрофически низкая … 5 — очень высокая).',
    panels: [
      {
        title: 'Удельная водообеспеченность населения (УДВn), тыс.м³/чел',
        candidates: ['Удельная водообеспеченность населения', 'УДВn'],
        unit: 'тыс.м³/чел',
        color: '#2563eb',
      },
      {
        title: 'Балл водообеспеченности населения',
        candidates: ['Удельная водообеспеченность населения', 'УДВn'],
        unit: 'тыс.м³/чел',
        color: '#2563eb',
        heatmap: 'waterSupplyPopulation',
      },
    ],
  },
  {
    id: 'area',
    title: 'По площади (УДВ(S))',
    description:
      'Удельная водообеспеченность на единицу площади района, тыс.м³/км², и её балл (0 — катастрофически низкая … 5 — очень высокая).',
    panels: [
      {
        title: 'Удельная водообеспеченность по площади (УДВ(S)), тыс.м³/км²',
        candidates: ['Удельная водообеспеченность по площади', 'УДВ(S)'],
        unit: 'тыс.м³/км²',
        color: '#0891b2',
      },
      {
        title: 'Балл водообеспеченности территории',
        candidates: ['Удельная водообеспеченность по площади', 'УДВ(S)'],
        unit: 'тыс.м³/км²',
        color: '#0891b2',
        heatmap: 'waterSupplyArea',
      },
    ],
  },
]

export default function WaterSupplyDashboardPage() {
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
        const dir = dirs.find((d) => d.code === 'water')
        if (!dir) {
          if (!cancel) setIndicators([])
          return
        }
        const inds = await fetchIndicators({ direction_id: dir.id, is_active: true })
        if (!cancel) setIndicators(inds)
      })
      .catch((e: unknown) => {
        if (!cancel) setError((e as { message?: string })?.message ?? 'Ошибка загрузки')
      })
      .finally(() => {
        if (!cancel) setLoading(false)
      })
    return () => {
      cancel = true
    }
  }, [])

  const resolve = useMemo(() => {
    const byLower = indicators.map((i) => ({ ind: i, lc: i.name.toLowerCase() }))
    return (candidates: string[]): Indicator | null => {
      for (const c of candidates) {
        const needle = c.toLowerCase()
        const hit = byLower.find((x) => x.lc.includes(needle))
        if (hit) return hit.ind
      }
      return null
    }
  }, [indicators])

  const currentTab = TABS.find((t) => t.id === tabId) ?? TABS[0]

  const kpiMetrics: ClimateKpiMetric[] = useMemo(() => {
    const pop = resolve(['Удельная водообеспеченность населения', 'УДВn'])?.id ?? null
    const area = resolve(['Удельная водообеспеченность по площади', 'УДВ(S)'])?.id ?? null
    return [
      { label: 'Средняя УДВn по районам', indicatorId: pop, agg: 'avg', unit: 'тыс.м³/чел', decimals: 1 },
      { label: 'Район с минимальной УДВn (зона риска)', indicatorId: pop, agg: 'argmin' },
      { label: 'Район с максимальной УДВn', indicatorId: pop, agg: 'argmax' },
      { label: 'Средняя УДВ(S) по районам', indicatorId: area, agg: 'avg', unit: 'тыс.м³/км²', decimals: 1 },
      { label: 'Район с минимальной УДВ(S)', indicatorId: area, agg: 'argmin' },
    ]
  }, [resolve])

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-bold text-gray-900">
          Водообеспеченность территории
        </h1>
        <p className="text-base text-gray-500 mt-1">
          Водные ресурсы · Северо-Казахстанская область · удельная водообеспеченность по населению и площади.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-base text-red-700">
          {error}
        </div>
      )}

      {!loading && <ClimateKpiStrip title="Ключевые показатели" metrics={kpiMetrics} />}

      <div className="border-b border-gray-200">
        <div className="flex gap-1 -mb-px overflow-x-auto">
          {TABS.map((t) => (
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

      {loading ? (
        <div className="p-6 text-base text-gray-500">Загрузка показателей…</div>
      ) : (
        <div className="space-y-6">
          {currentTab.description && (
            <p className="text-base text-gray-600">{currentTab.description}</p>
          )}
          <TabSummary
            tabId={currentTab.id}
            title={`Водные ресурсы — водообеспеченность: ${currentTab.title}`}
            directionCode="water"
            indicatorIds={currentTab.panels.map((p) => resolve(p.candidates)?.id)}
          />
          {currentTab.panels.map((panel, idx) => {
            const ind = resolve(panel.candidates)
            if (!ind) {
              return (
                <section
                  key={`${currentTab.id}-${idx}`}
                  className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5"
                >
                  <h3 className="text-base font-semibold text-gray-900 mb-2">{panel.title}</h3>
                  <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center text-base text-gray-500">
                    Показатель не найден в справочнике направления «Водные ресурсы».
                  </div>
                </section>
              )
            }
            return (
              <div key={`${currentTab.id}-${idx}`} className="space-y-2">
                <h3 className="text-base font-semibold text-gray-900">{panel.title}</h3>
                <IndicatorView
                  key={`${ind.id}-${panel.heatmap ?? 'plain'}`}
                  indicatorId={ind.id}
                  indicatorName={panel.title}
                  unit={ind.unit_name ?? panel.unit}
                  color={panel.color}
                  colorBins={panel.heatmap ? WATER_COLOR_SCALES[panel.heatmap].bins : undefined}
                />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
