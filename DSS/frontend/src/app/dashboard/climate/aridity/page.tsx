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
 * Климат — «Засушливость вегетационного периода» по гидротермическому коэффициенту
 * Селянинова ГТК(5-8) (предложения заказчика): формула, тепловая шкала по годам
 * (таблица 1), динамика, ранжирование и отклонение от нормы со шкалой заказчика
 * (±0,10 / 0,20 / 0,30 / 0,31 и более). ГТК(5-8) района — среднее по метеостанциям.
 * Показатель «Доля метеостанций района с ГТК<0,80» убран по замечанию заказчика.
 */

const GTK = 'Гидротермический коэффициент (ГТК)'

const TABS: ClimateTab[] = [
  {
    id: 'gtk',
    title: 'Гидротермический коэффициент ГТК(5-8)',
    before: <ClimateFormula kind="gtk" />,
    panels: [
      {
        title:
          'Засушливые условия на основе гидротермического коэффициента Селянинова ГТК(5-8) по районам Северо-Казахстанской области, {year} г.',
        name: 'Гидротермический коэффициент Селянинова ГТК(5-8)',
        indicator: GTK,
        unit: 'безразмерная величина',
        color: '#2563eb',
        heatmap: 'gtk',
        dynamics: 'Динамика гидротермического коэффициента Селянинова ГТК(5-8) {region}, {years}',
        ranking: 'Ранжирование районов по гидротермическому коэффициенту Селянинова ГТК(5-8), {year} г.',
      },
      {
        title: `Отклонение ГТК(5-8) в {year} г. от среднемноголетней нормы (${NORM_PERIOD_LABEL})`,
        name: 'Отклонение ГТК(5-8) от нормы',
        indicator: GTK,
        unit: 'безразмерная величина',
        color: '#2563eb',
        transform: 'deviationNorm',
        heatmap: 'gtkDeviation',
        dynamics: 'Динамика отклонения ГТК(5-8) от нормы {region}, {years}',
        ranking: 'Ранжирование районов по отклонению ГТК(5-8) от нормы, {year} г.',
      },
    ],
    after: (
      <GeoLayersPanel
        title="Зоны засушливости"
        description="Средние условия вегетационного периода 2003–2024 гг. по данным каталога геоданных."
        layers={['drought-zones']}
      />
    ),
  },
]

export default function ClimateAridityDashboardPage() {
  return (
    <ClimateSectionPage
      title="Засушливость вегетационного периода"
      tabs={TABS}
      summaryTitle="Климат — засушливость вегетационного периода"
      kpi={(resolve) => {
        const g = resolve(GTK)?.id ?? null
        return [
          { label: 'Средний ГТК(5-8) по области', indicatorId: g, agg: 'avg', decimals: 2 },
          { label: 'Максимальное значение ГТК(5-8)', indicatorId: g, agg: 'max', decimals: 2 },
          { label: 'Минимальное значение ГТК(5-8)', indicatorId: g, agg: 'min', decimals: 2 },
          { label: 'Район с максимальным ГТК(5-8)', indicatorId: g, agg: 'argmax' },
          { label: 'Район с минимальным ГТК(5-8)', indicatorId: g, agg: 'argmin' },
        ]
      }}
    />
  )
}
