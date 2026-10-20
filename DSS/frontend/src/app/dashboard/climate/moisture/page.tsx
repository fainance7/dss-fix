'use client'

import dynamic from 'next/dynamic'
import ClimateSectionPage, { type ClimateTab } from '@/components/dashboard/climate/ClimateSectionPage'
import ClimateFormula from '@/components/dashboard/climate/ClimateFormula'
import { NORM_PERIOD_LABEL } from '@/lib/climate/transforms'

// Карта из каталога геоданных: maplibre грузится только когда секция на экране.
const GeoLayersPanel = dynamic(() => import('@/components/dashboard/GeoLayersPanel'), {
  ssr: false,
  loading: () => <div className="h-[460px] animate-pulse rounded-xl bg-gray-100" />,
})

/**
 * Климат — «Влагообеспеченность вегетационного периода» по коэффициенту увлажнения
 * K(5-8) (предложения заказчика): формула и критерии, карта по классам, динамика,
 * ранжирование, отклонение от среднемноголетней нормы. K(5-8) района — среднее
 * по метеостанциям района (scripts/import_climate_workbook.py).
 * Показатель «Доля метеостанций района с K<0,80» убран по замечанию заказчика.
 */

const K = 'Коэффициент увлажнения (K)'

const TABS: ClimateTab[] = [
  {
    id: 'k',
    title: 'Коэффициент увлажнения K(5-8)',
    before: <ClimateFormula kind="k" />,
    panels: [
      {
        title:
          'Влагообеспеченность вегетационного периода (май–август) на основе коэффициента увлажнения K(5-8) по районам Северо-Казахстанской области, {year} г.',
        name: 'Коэффициент увлажнения K(5-8)',
        indicator: K,
        unit: 'безразмерная величина',
        color: '#2563eb',
        heatmap: 'kMoisture',
        dynamics: 'Динамика коэффициента увлажнения K(5-8) {region}, {years}',
        ranking: 'Ранжирование районов Северо-Казахстанской области по коэффициенту увлажнения K(5-8), {year} г.',
      },
      {
        title: `Отклонение коэффициента увлажнения K(5-8) в {year} г. от среднемноголетней нормы (${NORM_PERIOD_LABEL})`,
        name: 'Отклонение K(5-8) от нормы',
        indicator: K,
        unit: 'безразмерная величина',
        color: '#2563eb',
        transform: 'deviationNorm',
        heatmap: 'kDeviation',
        dynamics: 'Динамика отклонения коэффициента увлажнения K(5-8) от нормы {region}, {years}',
        ranking: 'Ранжирование районов по отклонению коэффициента увлажнения K(5-8) от нормы, {year} г.',
      },
    ],
    after: (
      <GeoLayersPanel
        title="Зоны влагообеспеченности"
        description="Средние условия вегетационного периода 2003–2024 гг. по данным каталога геоданных."
        layers={['moisture-zones']}
      />
    ),
  },
]

export default function ClimateMoistureDashboardPage() {
  return (
    <ClimateSectionPage
      title="Влагообеспеченность вегетационного периода"
      tabs={TABS}
      summaryTitle="Климат — влагообеспеченность вегетационного периода"
      kpi={(resolve) => {
        const k = resolve(K)?.id ?? null
        return [
          { label: 'Средний коэффициент увлажнения K(5-8) по области', indicatorId: k, agg: 'avg', decimals: 2 },
          { label: 'Максимальное значение K(5-8)', indicatorId: k, agg: 'max', decimals: 2 },
          { label: 'Минимальное значение K(5-8)', indicatorId: k, agg: 'min', decimals: 2 },
          { label: 'Район с максимальным K(5-8)', indicatorId: k, agg: 'argmax' },
          { label: 'Район с минимальным K(5-8)', indicatorId: k, agg: 'argmin' },
        ]
      }}
    />
  )
}
