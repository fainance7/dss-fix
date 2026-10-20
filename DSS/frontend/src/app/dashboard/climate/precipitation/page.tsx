'use client'

import dynamic from 'next/dynamic'
import ClimateSectionPage, {
  type ClimatePanel,
  type ClimateTab,
} from '@/components/dashboard/climate/ClimateSectionPage'
import MonthlyClimateCard from '@/components/dashboard/MonthlyClimateCard'
import type { ClimateScaleKey } from '@/lib/climate/colorScales'
import { NORM_PERIOD_LABEL } from '@/lib/climate/transforms'

// Карта из каталога геоданных: maplibre грузится только когда секция на экране.
const GeoLayersPanel = dynamic(() => import('@/components/dashboard/GeoLayersPanel'), {
  ssr: false,
  loading: () => <div className="h-[460px] animate-pulse rounded-xl bg-gray-100" />,
})

/**
 * Климат — «Атмосферные осадки» (предложения заказчика): сумма осадков за год,
 * вегетационный период (май–август), весну (апрель–май), осень (сентябрь–октябрь)
 * и зиму (ноябрь–март) — в мм, в % от нормы и отклонение от нормы, по районам и в
 * среднем по области. Суммы рассчитаны из месячных сумм осадков метеостанций
 * (месяц = сумма трёх декад); значение района — среднее станций района.
 */

const SKO = 'по районам Северо-Казахстанской области'

interface Period {
  id: string
  tab: string
  /** «за вегетационный период (май–август)» */
  phrase: string
  indicator: string
  deviationScale: ClimateScaleKey
  color: string
}

const PERIODS: Period[] = [
  {
    id: 'year',
    tab: 'За год',
    phrase: 'за год',
    indicator: 'Годовая сумма осадков',
    deviationScale: 'precipDeviationNorm',
    color: '#2563eb',
  },
  {
    id: 'veg',
    tab: 'Вегетационный период (май–август)',
    phrase: 'за вегетационный период (май–август)',
    indicator: 'Сумма осадков за вегетационный период',
    deviationScale: 'precipDeviationNorm',
    color: '#0d9488',
  },
  {
    id: 'spring',
    tab: 'Весна (апрель–май)',
    phrase: 'за весенний период (апрель–май)',
    indicator: 'Сумма осадков за весенний период (апрель–май)',
    deviationScale: 'precipDeviationNormSeason',
    color: '#16a34a',
  },
  {
    id: 'autumn',
    tab: 'Осень (сентябрь–октябрь)',
    phrase: 'за осенний период (сентябрь–октябрь)',
    indicator: 'Сумма осадков за осенний период (сентябрь–октябрь)',
    deviationScale: 'precipDeviationNormSeason',
    color: '#ca8a04',
  },
  {
    id: 'winter',
    tab: 'Зима (ноябрь–март)',
    phrase: 'за зимний период (ноябрь–март)',
    indicator: 'Сумма осадков за холодный период',
    deviationScale: 'precipDeviationNorm',
    color: '#0891b2',
  },
]

function panelsFor(p: Period): ClimatePanel[] {
  const panels: ClimatePanel[] = [
    {
      title: `Сумма атмосферных осадков ${p.phrase} ${SKO}, мм, {year} г.`,
      name: `Сумма осадков ${p.phrase}`,
      indicator: p.indicator,
      unit: 'мм',
      color: p.color,
      decimals: 1,
      dynamics: `Динамика суммы осадков ${p.phrase} {region}, мм, {years}`,
      ranking: `Ранжирование районов по сумме осадков ${p.phrase}, мм, {year} г.`,
    },
  ]
  if (p.id === 'veg') {
    panels.push({
      title: 'Классификация обеспеченности осадками за вегетационный период по сумме осадков, мм, {year} г.',
      name: 'Сумма осадков за вегетационный период (май–август)',
      indicator: p.indicator,
      unit: 'мм',
      color: p.color,
      decimals: 1,
      heatmap: 'precipVegClass',
      dynamics: 'Динамика суммы осадков за вегетационный период (май–август) {region}, мм, {years}',
      ranking: 'Ранжирование районов по сумме осадков за вегетационный период (май–август), мм, {year} г.',
    })
  }
  panels.push(
    {
      title: `Сумма осадков ${p.phrase} относительно среднемноголетней нормы (${NORM_PERIOD_LABEL}), %, {year} г.`,
      name: `Осадки ${p.phrase}, % от нормы`,
      indicator: p.indicator,
      unit: '%',
      color: p.color,
      decimals: 1,
      transform: 'pctOfNorm',
      heatmap: 'precipitationPct',
      dynamics: `Динамика обеспеченности осадками ${p.phrase} относительно нормы {region}, %, {years}`,
      ranking: `Ранжирование районов по обеспеченности осадками ${p.phrase} относительно нормы, %, {year} г.`,
    },
    {
      title: `Отклонение суммы осадков ${p.phrase} от среднемноголетней нормы (${NORM_PERIOD_LABEL}), мм, {year} г.`,
      name: `Отклонение суммы осадков ${p.phrase} от нормы`,
      indicator: p.indicator,
      unit: 'мм',
      color: p.color,
      decimals: 1,
      transform: 'deviationNorm',
      heatmap: p.deviationScale,
      dynamics: `Динамика отклонения суммы осадков ${p.phrase} от нормы {region}, мм, {years}`,
      ranking: `Ранжирование районов по отклонению суммы осадков ${p.phrase} от нормы, мм, {year} г.`,
    },
  )
  return panels
}

const TABS: ClimateTab[] = PERIODS.map((p) => ({
  id: p.id,
  title: p.tab,
  description:
    p.id === 'winter'
      ? 'Сумма осадков за ноябрь–декабрь предыдущего года и январь–март текущего года.'
      : undefined,
  before:
    p.id === 'year' ? (
      <MonthlyClimateCard
        title="Сумма осадков по месяцам, мм"
        indicator="precipitation"
        description={`Месячная сумма осадков — сумма трёх декад; норма — среднемноголетнее значение месяца (${NORM_PERIOD_LABEL}).`}
      />
    ) : undefined,
  panels: panelsFor(p),
  after:
    p.id === 'veg' ? (
      <GeoLayersPanel
        title="Зоны атмосферных осадков"
        description="Суммы осадков за вегетационный и холодный периоды, средние условия 2003–2024 гг."
        layers={['precip-veg-zones', 'precip-cold-zones']}
      />
    ) : undefined,
}))

export default function ClimatePrecipitationDashboardPage() {
  return (
    <ClimateSectionPage
      title="Атмосферные осадки"
      tabs={TABS}
      summaryTitle="Климат — атмосферные осадки"
      kpi={(resolve) => {
        const year = resolve('Годовая сумма осадков')?.id ?? null
        const veg = resolve('Сумма осадков за вегетационный период')?.id ?? null
        const cold = resolve('Сумма осадков за холодный период')?.id ?? null
        return [
          { label: 'Средняя годовая сумма осадков по области', indicatorId: year, agg: 'avg', unit: 'мм', decimals: 1 },
          { label: 'Средняя сумма осадков за вегетационный период (май–август)', indicatorId: veg, agg: 'avg', unit: 'мм', decimals: 1 },
          { label: 'Средняя сумма осадков за зимний период (ноябрь–март)', indicatorId: cold, agg: 'avg', unit: 'мм', decimals: 1 },
          { label: 'Район с максимальной суммой осадков за вегетационный период', indicatorId: veg, agg: 'argmax' },
          { label: 'Район с минимальной суммой осадков за вегетационный период', indicatorId: veg, agg: 'argmin' },
        ]
      }}
    />
  )
}
