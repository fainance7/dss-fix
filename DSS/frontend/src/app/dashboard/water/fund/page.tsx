'use client'

import { useEffect, useMemo, useState } from 'react'
import { clsx } from 'clsx'
import IndicatorView from '@/components/dashboard/IndicatorView'
import TabSummary from '@/components/dashboard/TabSummary'
import dynamic from 'next/dynamic'
import ClimateKpiStrip, { type ClimateKpiMetric } from '@/components/dashboard/ClimateKpiStrip'
import { fetchDirections, fetchIndicators, type Indicator } from '@/lib/api/backend'

// Карта из каталога геоданных: maplibre грузится только когда секция на экране.
const GeoLayersPanel = dynamic(() => import('@/components/dashboard/GeoLayersPanel'), {
  ssr: false,
  loading: () => <div className="h-[460px] animate-pulse rounded-xl bg-gray-100" />,
})

/**
 * Водный дашборд «Водохозяйственный фонд и гидрография» (docs/dashboards/water,
 * дашборды Д1–Д8). Карта районов СКО + динамика выбранного района + сравнение
 * районов за год (IndicatorView) по категориям водных объектов: озёра, болота,
 * другие водоёмы, реки и притоки.
 *
 * Данные по числу озёр/болот/водоёмов уже загружены (срез 2024); притоки/реки/
 * водохранилище наполнятся через intake-portal — до этого панели показывают
 * аккуратный empty-state.
 */

interface MapPanel {
  title: string
  candidates: string[]
  unit?: string
  color: string
}

interface TabSpec {
  id: string
  title: string
  description?: string
  panels: MapPanel[]
}

const TABS: TabSpec[] = [
  {
    id: 'lakes',
    title: 'Озёра',
    description:
      'Распределение озёр по районам области по категориям площади.',
    panels: [
      {
        title: 'Озёр площадью более 100 га, шт',
        candidates: ['Озёр площадью более 100'],
        unit: 'шт',
        color: '#1e3a8a',
      },
      {
        title: 'Озёр от 30 до 50 га, шт',
        candidates: ['Озёр от 30 до 50'],
        unit: 'шт',
        color: '#2563eb',
      },
      {
        title: 'Озёр площадью до 100 га, шт',
        candidates: ['Озёр площадью до 100'],
        unit: 'шт',
        color: '#38bdf8',
      },
      {
        title: 'Малых озёр, шт',
        candidates: ['Малых озёр'],
        unit: 'шт',
        color: '#0891b2',
      },
    ],
  },
  {
    id: 'wetlands',
    title: 'Болота и прочие водоёмы',
    description:
      'Болота и прочие водоёмы по районам области.',
    panels: [
      {
        title: 'Болот, шт',
        candidates: ['Болот'],
        unit: 'шт',
        color: '#16a34a',
      },
      {
        title: 'Других водоёмов, шт',
        candidates: ['Других водоёмов'],
        unit: 'шт',
        color: '#9333ea',
      },
    ],
  },
  {
    id: 'rivers',
    title: 'Реки и притоки',
    description:
      'Речная сеть бассейна р. Ишим (Есиль): крупная река, притоки, водохранилища.',
    panels: [
      {
        title: 'Наличие притоков реки, шт',
        candidates: ['Наличие притоков реки', 'притоков'],
        unit: 'шт',
        color: '#0e7490',
      },
    ],
  },
]

export default function WaterFundDashboardPage() {
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
    const big = resolve(['Озёр площадью более 100'])?.id ?? null
    const small = resolve(['Малых озёр'])?.id ?? null
    const others = resolve(['Других водоёмов'])?.id ?? null
    return [
      { label: 'Среднее число крупных озёр (>100 га) на район', indicatorId: big, agg: 'avg', unit: 'шт', decimals: 0 },
      { label: 'Район-лидер по крупным озёрам', indicatorId: big, agg: 'argmax' },
      { label: 'Среднее число малых озёр на район', indicatorId: small, agg: 'avg', unit: 'шт', decimals: 0 },
      { label: 'Район-лидер по прочим водоёмам', indicatorId: others, agg: 'argmax' },
    ]
  }, [resolve])

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-bold text-gray-900">
          Водохозяйственный фонд и гидрография
        </h1>
        <p className="text-base text-gray-500 mt-1">
          Водные ресурсы · Северо-Казахстанская область · карта районов, динамика и сравнение по году.
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
            title={`Водные ресурсы — водный фонд: ${currentTab.title}`}
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
                  key={ind.id}
                  indicatorId={ind.id}
                  indicatorName={panel.title}
                  unit={ind.unit_name ?? panel.unit}
                  color={panel.color}
                />
              </div>
            )
          })}
        </div>
      )}

      <GeoLayersPanel
        title="Гидрографическая сеть"
        description="Озёра, реки и солончаки области."
        layers={['lakes', 'rivers', 'salt-marshes']}
        initial={['lakes', 'rivers']}
      />
    </div>
  )
}
