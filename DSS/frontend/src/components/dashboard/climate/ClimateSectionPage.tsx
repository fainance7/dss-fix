'use client'

/**
 * Страница раздела «Климат» (термические ресурсы, осадки, влагообеспеченность,
 * засушливость): вкладки, KPI, вывод ИИ и панели показателей. Каждая панель —
 * карта районов + динамика выбранного района + ранжирование районов (IndicatorView)
 * с заголовками в формулировках заказчика: показатель, территория, период, единица.
 * Норма — среднее района за базовый период 2003–2025 гг. (lib/climate/transforms).
 */

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { clsx } from 'clsx'
import IndicatorView from '@/components/dashboard/IndicatorView'
import TabSummary from '@/components/dashboard/TabSummary'
import ClimateKpiStrip, { type ClimateKpiMetric } from '@/components/dashboard/ClimateKpiStrip'
import { CLIMATE_COLOR_SCALES, type ClimateScaleKey } from '@/lib/climate/colorScales'
import {
  NORM_PERIOD,
  deviationFromNorm,
  deviationFromNormPct,
  pctOfNorm,
} from '@/lib/climate/transforms'
import { fetchDirections, fetchIndicators, type Indicator } from '@/lib/api/backend'

export interface ClimatePanel {
  /** Заголовок карты; {year} — выбранный год. */
  title: string
  /** Краткое название показателя (хедер панели). */
  name: string
  /** Точное название показателя или его уникальная подстрока. */
  indicator: string
  unit: string
  color: string
  heatmap?: ClimateScaleKey
  transform?: 'pctOfNorm' | 'deviationNorm' | 'deviationNormPct'
  /** Шаблоны заголовков: {region} — «в Айыртауском районе», {years}, {year}. */
  dynamics: string
  ranking: string
  /** Знаков после запятой (по умолчанию 2). */
  decimals?: number
}

export interface ClimateTab {
  id: string
  title: string
  description?: string
  /** Блоки перед панелями (формула, помесячный график). */
  before?: ReactNode
  panels: ClimatePanel[]
  /** Блоки после панелей (справочные таблицы). */
  after?: ReactNode
}

export type ResolveIndicator = (name: string) => Indicator | null

const TRANSFORMS = {
  pctOfNorm,
  deviationNorm: deviationFromNorm,
  deviationNormPct: deviationFromNormPct,
} as const

export default function ClimateSectionPage({
  title,
  subtitle,
  tabs,
  kpi,
  summaryTitle,
}: {
  title: string
  subtitle?: string
  tabs: ClimateTab[]
  /** KPI раздела по найденным показателям. */
  kpi?: (resolve: ResolveIndicator) => ClimateKpiMetric[]
  /** Префикс названия для вывода ИИ: «Климат — термические ресурсы». */
  summaryTitle: string
}) {
  const [tabId, setTabId] = useState<string>(tabs[0].id)
  const [indicators, setIndicators] = useState<Indicator[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancel = false
    fetchDirections()
      .then(async (dirs) => {
        const dir = dirs.find((d) => d.code === 'climate')
        const inds = dir ? await fetchIndicators({ direction_id: dir.id, is_active: true }) : []
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

  // Сначала точное совпадение названия, затем — подстрока.
  const resolve: ResolveIndicator = useMemo(() => {
    return (name: string) => {
      const lc = name.toLowerCase()
      return (
        indicators.find((i) => i.name.toLowerCase() === lc) ??
        indicators.find((i) => i.name.toLowerCase().includes(lc)) ??
        null
      )
    }
  }, [indicators])

  const tab = tabs.find((t) => t.id === tabId) ?? tabs[0]
  const kpiMetrics = useMemo(() => (kpi ? kpi(resolve) : []), [kpi, resolve])

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-bold text-gray-900">{title}</h1>
        <p className="mt-1 text-base text-gray-500">
          {subtitle ??
            `Климат · Северо-Казахстанская область · по данным метеостанций РГП «Казгидромет»; норма — среднемноголетнее значение за ${NORM_PERIOD[0]}–${NORM_PERIOD[1]} гг.`}
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-base text-red-700">
          {error}
        </div>
      )}

      {!loading && kpiMetrics.length > 0 && (
        <ClimateKpiStrip title="Ключевые показатели" metrics={kpiMetrics} />
      )}

      {tabs.length > 1 && (
        <div className="border-b border-gray-200">
          <div className="-mb-px flex gap-1 overflow-x-auto">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => setTabId(t.id)}
                className={clsx(
                  'whitespace-nowrap border-b-2 px-4 py-2.5 text-base font-medium transition-colors',
                  tabId === t.id
                    ? 'border-primary-600 text-primary-700'
                    : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700',
                )}
              >
                {t.title}
              </button>
            ))}
          </div>
        </div>
      )}

      {loading ? (
        <div className="p-6 text-base text-gray-500">Загрузка показателей…</div>
      ) : (
        <div className="space-y-6">
          {tab.description && <p className="text-base text-gray-600">{tab.description}</p>}
          {tab.panels.length > 0 && (
            <TabSummary
              tabId={tab.id}
              title={`${summaryTitle}: ${tab.title}`}
              directionCode="climate"
              indicatorIds={tab.panels.map((p) => resolve(p.indicator)?.id)}
            />
          )}
          {tab.before}
          {tab.panels.map((panel, idx) => {
            const ind = resolve(panel.indicator)
            if (!ind) {
              return (
                <section
                  key={`${tab.id}-${idx}`}
                  className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100"
                >
                  <h3 className="mb-2 text-base font-semibold text-gray-900">
                    {panel.title.replace('{year}', '')}
                  </h3>
                  <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center text-base text-gray-500">
                    Показатель не найден в справочнике направления «Климат».
                  </div>
                </section>
              )
            }
            const scale = panel.heatmap ? CLIMATE_COLOR_SCALES[panel.heatmap] : undefined
            return (
              <IndicatorView
                key={`${tab.id}-${idx}-${ind.id}`}
                indicatorId={ind.id}
                indicatorName={panel.name}
                unit={panel.unit}
                color={panel.color}
                colorBins={scale?.bins}
                legendTitle={scale?.name}
                valueTransform={panel.transform ? TRANSFORMS[panel.transform] : undefined}
                mapTitle={panel.title}
                titles={{ dynamics: panel.dynamics, ranking: panel.ranking }}
                decimals={panel.decimals ?? 2}
                normPeriod={NORM_PERIOD}
                diverging={panel.transform === 'deviationNorm' || panel.transform === 'deviationNormPct'}
                showTrend={!panel.transform}
                showNorm={!panel.transform}
                dynamicsChart="line"
              />
            )
          })}
          {tab.after}
        </div>
      )}
    </div>
  )
}
