'use client'

import { useEffect, useMemo, useState } from 'react'
import { clsx } from 'clsx'
import IndicatorView from '@/components/dashboard/IndicatorView'
import TabSummary from '@/components/dashboard/TabSummary'
import dynamic from 'next/dynamic'
import { fetchDirections, fetchIndicators, type Indicator } from '@/lib/api/backend'

// Карта из каталога геоданных: maplibre грузится только когда секция на экране.
const GeoLayersPanel = dynamic(() => import('@/components/dashboard/GeoLayersPanel'), {
  ssr: false,
  loading: () => <div className="h-[460px] animate-pulse rounded-xl bg-gray-100" />,
})

// Спецификация дашборда «Демографическое развитие региона»
// (docs/dashboards/social_demog/Показатели дашборда Демография и Трудовые ресурсы.docx).
// Каждая вкладка = один показатель. Имя должно совпадать с indicators.name в БД.
interface TabSpec {
  id: string
  title: string
  indicatorName: string
  unit?: string
  color: string
  /**
   * Если задан — панель динамики показывает линейный график с этими показателями
   * (несколько линий) вместо столбчатой диаграммы по одному показателю.
   */
  lineSeriesNames?: string[]
}

// Цвета линий мульти-графика по имени показателя.
const LINE_SERIES_COLORS: Record<string, string> = {
  'Общий коэффициент рождаемости': '#16a34a',
  'Общий коэффициент смертности': '#dc2626',
  'Коэффициент естественного прироста': '#0891b2',
}

const TABS: TabSpec[] = [
  {
    id: 'population',
    title: 'Численность населения',
    indicatorName: 'Численность населения (всего)',
    unit: 'чел.',
    color: '#2563eb',
  },
  {
    id: 'aging',
    title: 'Доля 65 лет и старше',
    indicatorName: 'Доля населения в возрасте 65 лет и старше',
    unit: '%',
    color: '#9333ea',
  },
  {
    id: 'birth',
    title: 'Коэф. рождаемости',
    indicatorName: 'Общий коэффициент рождаемости',
    unit: 'на 1000 нас.',
    color: '#16a34a',
  },
  {
    id: 'death',
    title: 'Коэф. смертности',
    indicatorName: 'Общий коэффициент смертности',
    unit: 'на 1000 нас.',
    color: '#dc2626',
  },
  {
    id: 'natural',
    title: 'Естественный прирост',
    indicatorName: 'Коэффициент естественного прироста',
    unit: 'на 1000 нас.',
    color: '#0891b2',
    lineSeriesNames: [
      'Общий коэффициент рождаемости',
      'Общий коэффициент смертности',
      'Коэффициент естественного прироста',
    ],
  },
  {
    id: 'migration',
    title: 'Миграционный прирост',
    indicatorName: 'Миграционный прирост (убыль) населения',
    unit: 'чел.',
    color: '#ea580c',
  },
  {
    id: 'migration_coef',
    title: 'Коэф. миграции',
    indicatorName: 'Коэффициент миграционного прироста',
    unit: 'на 1000 нас.',
    color: '#f59e0b',
  },
]

export default function DemographyDashboardPage() {
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
        const dir = dirs.find((d) => d.code === 'social_demog')
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

  const indicatorByName = useMemo(() => {
    const m = new Map<string, Indicator>()
    for (const i of indicators) m.set(i.name, i)
    return m
  }, [indicators])

  const currentTab = TABS.find((t) => t.id === tabId) ?? TABS[0]
  const currentIndicator = indicatorByName.get(currentTab.indicatorName)

  // Резолв имён показателей серии в индикаторы (отбрасываем ненайденные).
  const lineSeries = useMemo(() => {
    if (!currentTab.lineSeriesNames) return undefined
    const resolved = currentTab.lineSeriesNames
      .map((name) => {
        const ind = indicatorByName.get(name)
        if (!ind) return null
        return { id: ind.id, name: ind.name, color: LINE_SERIES_COLORS[name] ?? '#64748b' }
      })
      .filter((s): s is { id: string; name: string; color: string } => s !== null)
    return resolved.length > 0 ? resolved : undefined
  }, [currentTab, indicatorByName])

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-bold text-gray-900">Демографическое развитие региона</h1>
        <p className="text-base text-gray-500 mt-1">
          Северо-Казахстанская область · 7 показателей · карта районов, динамика и
          сравнение районов по выбранному году.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-base text-red-700">
          {error}
        </div>
      )}

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

      {!loading && currentIndicator && (
        <TabSummary
          tabId={currentTab.id}
          title={`Демографическое развитие региона: ${currentTab.title}`}
          directionCode="social_demog"
          indicatorIds={[currentIndicator.id, ...(lineSeries ?? []).map((x) => x.id)]}
        />
      )}

      {loading ? (
        <div className="p-6 text-base text-gray-500">Загрузка показателей…</div>
      ) : !currentIndicator ? (
        <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center text-base text-gray-500">
          Показатель «{currentTab.indicatorName}» не найден в справочнике.
        </div>
      ) : (
        <IndicatorView
          key={currentIndicator.id}
          indicatorId={currentIndicator.id}
          indicatorName={currentIndicator.name}
          unit={currentIndicator.unit_name ?? currentTab.unit}
          color={currentTab.color}
          lineSeries={lineSeries}
        />
      )}

      <GeoLayersPanel
        title="Сеть населённых пунктов"
        description="Контуры населённых пунктов области по районам (данные о численности и типе поселения в слое отсутствуют)."
        layers={['settlements']}
      />
    </div>
  )
}
