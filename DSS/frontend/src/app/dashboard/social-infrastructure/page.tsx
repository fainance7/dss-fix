'use client'

import { useEffect, useMemo, useState } from 'react'
import { clsx } from 'clsx'
import IndicatorView from '@/components/dashboard/IndicatorView'
import TabSummary from '@/components/dashboard/TabSummary'
import { fetchDirections, fetchIndicators, type Indicator } from '@/lib/api/backend'

// Спецификация: docs/dashboards/social_demog — дашборд №3 «Социальная
// инфраструктура и качество жизни». 8 показателей = 8 вкладок (1:1).
interface TabSpec {
  id: string
  title: string
  indicatorName: string
  unit?: string
  color: string
  /** Комбо-диаграмма: линия этого показателя на правой оси поверх столбцов. */
  comboLineName?: string
  comboLineColor?: string
}

const TABS: TabSpec[] = [
  {
    id: 'living-wage',
    title: 'Прожиточный минимум',
    indicatorName: 'Величина прожиточного минимума',
    unit: 'тенге',
    color: '#0891b2',
    comboLineName: 'Среднемесячная номинальная заработная плата',
    comboLineColor: '#9333ea',
  },
  {
    id: 'preschool',
    title: 'Дошкольные организации',
    indicatorName: 'Число дошкольных организаций',
    unit: 'единиц',
    color: '#16a34a',
  },
  {
    id: 'schools',
    title: 'Школы',
    indicatorName: 'Число дневных общеобразовательных школ',
    unit: 'единиц',
    color: '#2563eb',
  },
  {
    id: 'universities',
    title: 'ВУЗы',
    indicatorName: 'Число организаций высшего и послевузовского образования',
    unit: 'единиц',
    color: '#9333ea',
  },
  {
    id: 'doctors',
    title: 'Врачи на 10 тыс. нас.',
    indicatorName: 'Численность врачей всех специальностей на 10 000 населения',
    unit: 'ед.',
    color: '#dc2626',
  },
  {
    id: 'hospitals',
    title: 'Больничные организации',
    indicatorName: 'Число больничных организаций',
    unit: 'единиц',
    color: '#ea580c',
  },
  {
    id: 'housing',
    title: 'Обеспеченность жильём',
    indicatorName: 'Обеспеченность жильём на 1 человека',
    unit: 'м²',
    color: '#0d9488',
  },
  {
    id: 'innovation',
    title: 'Инновационная активность',
    indicatorName: 'Инновационная активность предприятий',
    unit: '%',
    color: '#f59e0b',
  },
]

export default function SocialInfrastructureDashboardPage() {
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
        // Дополнительно грузим показатели labor — для combo-линии «Среднемесячная
        // номинальная заработная плата» на вкладке «Прожиточный минимум».
        const laborDir = dirs.find((d) => d.code === 'labor')
        const [inds, laborInds] = await Promise.all([
          fetchIndicators({ direction_id: dir.id, is_active: true }),
          laborDir
            ? fetchIndicators({ direction_id: laborDir.id, is_active: true })
            : Promise.resolve([] as Indicator[]),
        ])
        if (!cancel) setIndicators([...inds, ...laborInds])
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

  // Резолв combo-линии (может жить в другом направлении — labor).
  const comboLine = useMemo(() => {
    if (!currentTab.comboLineName) return undefined
    const ind = indicatorByName.get(currentTab.comboLineName)
    return ind
      ? {
          id: ind.id,
          name: ind.name,
          color: currentTab.comboLineColor ?? '#9333ea',
          unit: ind.unit_name ?? undefined,
        }
      : undefined
  }, [currentTab, indicatorByName])

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-bold text-gray-900">
          Социальная инфраструктура и качество жизни
        </h1>
        <p className="text-base text-gray-500 mt-1">
          Северо-Казахстанская область · 8 показателей · карта районов, динамика и
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
          title={`Социальная инфраструктура и качество жизни: ${currentTab.title}`}
          directionCode="social_demog"
          indicatorIds={[currentIndicator.id]}
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
          comboLine={comboLine}
        />
      )}
    </div>
  )
}
