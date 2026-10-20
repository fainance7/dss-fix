'use client'

import DirectionDashboard, {
  type DashboardTab,
} from '@/components/dashboard/DirectionDashboard'

const TABS: DashboardTab[] = [
  {
    id: 'roads',
    title: 'Дорожная сеть',
    description: 'Протяжённость дорог республиканского и местного значения.',
    blocks: [
      {
        title: 'Дорожная сеть',
        type: 'kpi',
        indicators: ['Состояние и протяжённость дорожной сети'],
      },
      {
        title: 'Карта дорог СКО',
        type: 'geo',
        description: 'Автомобильные дороги и населённые пункты области.',
        geoLayers: ['roads', 'settlements'],
        geoInitial: ['roads', 'settlements'],
      },
    ],
  },
  {
    id: 'transport',
    title: 'Перевозки и грузооборот',
    description: 'Динамика перевозок грузов и пассажиров 2022–2024.',
    blocks: [
      {
        title: 'Объёмы перевозок',
        type: 'kpi',
        indicators: [
          'Перевозки грузов',
          'Перевозки пассажиров',
          'Грузооборот',
          'Пассажирооборот',
        ],
      },
      {
        title: 'Динамика перевозок',
        type: 'line',
        indicators: ['Перевозки грузов', 'Перевозки пассажиров'],
      },
    ],
  },
  {
    id: 'fleet',
    title: 'Подвижной состав',
    description: 'Транспортные средства и сельхозтехника по районам.',
    blocks: [
      {
        title: 'Подвижной состав',
        type: 'kpi',
        indicators: [
          'Наличие подвижного состава',
          'Сельскохозяйственная техника',
        ],
      },
      {
        title: 'Heatmap с/х техники по 13 районам × типам',
        type: 'placeholder',
        vizHint: 'Heatmap или stacked bar chart по 60+ типам техники.',
        missingReason: 'Нет точечных данных о размещении техники (координаты/плотность).',
      },
    ],
  },
]

export default function TransportDashboardPage() {
  return (
    <DirectionDashboard
      directionCode="transport"
      directionLabel="Транспорт"
      tabs={TABS}
    />
  )
}
