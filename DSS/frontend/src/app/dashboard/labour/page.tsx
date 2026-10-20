'use client'

import { useEffect, useMemo, useState } from 'react'
import { clsx } from 'clsx'
import IndicatorView from '@/components/dashboard/IndicatorView'
import TabSummary from '@/components/dashboard/TabSummary'
import {
  fetchDirections,
  fetchIndicators,
  fetchEconomicSectors,
  type Indicator,
  type EconomicSector,
} from '@/lib/api/backend'

// Спецификация: docs/dashboards/social_demog — дашборд №2 «Трудовые ресурсы и
// занятость». Базовые вкладки = 1 показатель = 1 вкладка, каждая — карта районов
// + динамика выбранного района + ранжирование районов за год. На вкладке
// «Среднемесячная ЗП» под картой дополнительно показываем ЗП по секторам
// экономики горизонтальной гистограммой (замечание заказчика: не отдельной
// вкладкой, а прямо на карте показателя; см. sectorBars).
interface TabSpec {
  id: string
  title: string
  indicatorName: string
  unit?: string
  color: string
  /** Стопки (накопление) на обеих диаграммах из этих показателей. */
  stackedSeriesNames?: string[]
  /** Линейный график с несколькими линиями на панели динамики. */
  lineSeriesNames?: string[]
  /**
   * Если true — под картой и базовыми диаграммами добавляется горизонтальная
   * гистограмма «ЗП по секторам экономики» за выбранный год. Серии берутся из
   * справочника `economic_sectors` (см. WAGE_SECTOR_OKED): каждая — показатель ЗП
   * сектора по `indicator_name`, подпись и цвет из справочника.
   */
  sectorBars?: boolean
  /**
   * Если true — поверх карты в центроиде каждого района рисуется кольцевая
   * диаграмма: сегменты — доли занятости по 3 группам ВЭД (см. DONUT_PARTS),
   * диаметр — «Занятое население» (см. DONUT_SIZE_INDICATOR).
   */
  donut?: boolean
}

// Сектора экономики для горизонтальной гистограммы ЗП на вкладке «Среднемесячная
// ЗП». Состав и порядок — по спецификации заказчика (12 секторов, включая агрегат
// «Промышленность» B–E; без под-секторов B/C/D/E и сфер I/K/L/N). Матчинг по ОКЭД.
const WAGE_SECTOR_OKED = ['A', 'B-E', 'F', 'G', 'H', 'J', 'M', 'O', 'P', 'Q', 'R', 'S']

// Кольцевые диаграммы на вкладке ЭАН: диаметр = «Занятое население», сегменты =
// занятость по 3 группам ВЭД (показатели импортированы джобом dss_upload_v8).
const DONUT_SIZE_INDICATOR = 'Занятое население'
const DONUT_PARTS: { indicatorName: string; name: string; color: string }[] = [
  {
    indicatorName:
      'Занятость населения по группам видов экономической деятельности — Сельское, лесное и рыбное хозяйство',
    name: 'Сельское хозяйство',
    color: '#16a34a',
  },
  {
    indicatorName:
      'Занятость населения по группам видов экономической деятельности — Промышленность и строительство',
    name: 'Промышленность',
    color: '#f59e0b',
  },
  {
    indicatorName:
      'Занятость населения по группам видов экономической деятельности — Сфера услуг',
    name: 'Сфера услуг',
    color: '#2563eb',
  },
]

// Цвета серий по имени показателя (стопки/линии).
const SERIES_COLORS: Record<string, string> = {
  'Численность наёмных работников': '#2563eb',
  'Численность самозанятых': '#f59e0b',
  'Уровень безработицы': '#ea580c',
  'Уровень молодёжной безработицы': '#7c3aed',
}

const TABS: TabSpec[] = [
  {
    id: 'eap',
    title: 'Эконом. активное население',
    indicatorName: 'Экономически активное население',
    unit: 'чел.',
    color: '#2563eb',
    donut: true,
  },
  {
    id: 'employed',
    title: 'Занятое население',
    indicatorName: 'Занятое население',
    unit: 'чел.',
    color: '#16a34a',
    stackedSeriesNames: ['Численность наёмных работников', 'Численность самозанятых'],
  },
  {
    id: 'employment_rate',
    title: 'Уровень занятости',
    indicatorName: 'Уровень занятости населения',
    unit: '%',
    color: '#0d9488',
  },
  {
    id: 'unemployed',
    title: 'Безработное население',
    indicatorName: 'Безработное население',
    unit: 'чел.',
    color: '#dc2626',
  },
  {
    id: 'unemployment_rate',
    title: 'Уровень безработицы',
    indicatorName: 'Уровень безработицы',
    unit: '%',
    color: '#ea580c',
    lineSeriesNames: ['Уровень безработицы', 'Уровень молодёжной безработицы'],
  },
  {
    id: 'wage',
    title: 'Среднемесячная ЗП',
    indicatorName: 'Среднемесячная номинальная заработная плата',
    unit: 'тенге',
    color: '#9333ea',
    sectorBars: true,
  },
]

export default function LabourDashboardPage() {
  const [tabId, setTabId] = useState<string>(TABS[0].id)
  const [indicators, setIndicators] = useState<Indicator[]>([])
  const [sectors, setSectors] = useState<EconomicSector[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancel = false
    setLoading(true)
    setError(null)
    fetchEconomicSectors({ is_active: true })
      .then((s) => {
        if (!cancel) setSectors(s)
      })
      .catch(() => {
        /* справочник секторов — best-effort: без него вкладка ЗП по секторам пустует */
      })
    fetchDirections()
      .then(async (dirs) => {
        const dir = dirs.find((d) => d.code === 'labor')
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

  // Резолв имён серий в индикаторы (с цветами); ненайденные отбрасываем.
  const resolveSeries = (names?: string[]) => {
    if (!names) return undefined
    const r = names
      .map((name) => {
        const ind = indicatorByName.get(name)
        return ind ? { id: ind.id, name: ind.name, color: SERIES_COLORS[name] ?? '#64748b' } : null
      })
      .filter((s): s is { id: string; name: string; color: string } => s !== null)
    return r.length > 0 ? r : undefined
  }
  const stackedSeries = useMemo(
    () => resolveSeries(currentTab.stackedSeriesNames),
    [currentTab, indicatorByName],
  )

  // Серии секторов экономики для горизонтальной гистограммы ЗП: показатель ЗП
  // каждого сектора (по indicator_name), подпись (короткая), полное имя и цвет —
  // из справочника. Состав/порядок секторов — WAGE_SECTOR_OKED.
  const sectorBars = useMemo(() => {
    const byCode = new Map(sectors.map((s) => [s.oked_code, s]))
    const r = WAGE_SECTOR_OKED.map((code) => byCode.get(code))
      .filter((s): s is EconomicSector => Boolean(s))
      .map((s) => {
        const ind = indicatorByName.get(s.indicator_name)
        return ind
          ? { id: ind.id, name: s.short_name, fullName: s.name, color: s.color }
          : null
      })
      .filter(
        (s): s is { id: string; name: string; fullName: string; color: string } => s !== null,
      )
    return r.length > 0 ? r : undefined
  }, [sectors, indicatorByName])

  const lineSeries = useMemo(
    () => resolveSeries(currentTab.lineSeriesNames),
    [currentTab, indicatorByName],
  )

  // Кольцевые диаграммы: показатель размера + 3 части по ВЭД. Резолвим в id;
  // если каких-то показателей нет в каталоге — overlay не строим.
  const donutOverlay = useMemo(() => {
    const sizeInd = indicatorByName.get(DONUT_SIZE_INDICATOR)
    if (!sizeInd) return undefined
    const parts = DONUT_PARTS.map((p) => {
      const ind = indicatorByName.get(p.indicatorName)
      return ind ? { id: ind.id, name: p.name, color: p.color } : null
    }).filter((p): p is { id: string; name: string; color: string } => p !== null)
    if (parts.length === 0) return undefined
    return { size: { id: sizeInd.id, name: sizeInd.name }, parts }
  }, [indicatorByName])

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-bold text-gray-900">Трудовые ресурсы и занятость</h1>
        <p className="text-base text-gray-500 mt-1">
          Северо-Казахстанская область · {TABS.length} вкладок · карта районов, динамика и
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
          title={`Трудовые ресурсы и занятость: ${currentTab.title}`}
          directionCode="labor"
          indicatorIds={[currentIndicator.id, ...(stackedSeries ?? []).map((x) => x.id), ...(lineSeries ?? []).map((x) => x.id)]}
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
          stackedSeries={stackedSeries}
          lineSeries={lineSeries}
          sectorBars={currentTab.sectorBars ? sectorBars : undefined}
          donutOverlay={currentTab.donut ? donutOverlay : undefined}
        />
      )}
    </div>
  )
}
