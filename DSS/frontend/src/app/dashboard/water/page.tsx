'use client'

import DirectionDashboard, {
  type DashboardTab,
} from '@/components/dashboard/DirectionDashboard'

const TABS: DashboardTab[] = [
  {
    id: 'fund',
    title: 'Водохозяйственный фонд',
    description: '2447 водных объектов: 2 водохранилища, р. Ишим + 78 притоков, озёра, болота.',
    blocks: [
      {
        title: 'Водные объекты СКО',
        type: 'kpi',
        indicators: [
          'Наличие крупного водохранилища',
          'Наличие крупной реки',
          'Наличие притоков реки',
          'Озёр площадью более 100 га',
          'Малых озёр',
        ],
      },
      {
        title: 'Гидрография по категориям',
        type: 'table',
        indicators: [
          'Озёр площадью более 100 га',
          'Озёр от 30 до 50 га',
          'Озёр площадью до 100 га',
          'Малых озёр',
          'Других водоёмов',
          'Болот',
        ],
      },
      {
        title: 'Озёра площадью более 100 га по районам',
        type: 'choropleth',
        indicators: ['Озёр площадью более 100 га'],
      },
    ],
  },
  {
    id: 'supply',
    title: 'Удельная водообеспеченность',
    description: 'УДВn (по населению) и УДВ(S) (по площади).',
    blocks: [
      {
        title: 'Водообеспеченность',
        type: 'kpi',
        indicators: [
          'Удельная водообеспеченность населения',
          'Удельная водообеспеченность по площади',
        ],
      },
      {
        title: 'Сравнение районов',
        type: 'table',
        indicators: ['Удельная водообеспеченность'],
      },
      {
        title: 'Картограмма водообеспеченности',
        type: 'placeholder',
        description: 'Балльная раскраска районов (0 — катастрофически низкая … 5 — очень высокая) — в детальном дашборде.',
        vizHint: 'Открыть «Водообеспеченность» → /dashboard/water/supply',
        missingReason: 'Нет одобренных значений УДВn/УДВ(S) по районам; балльная карта — в /dashboard/water/supply.',
      },
    ],
  },
  {
    id: 'quality',
    title: 'Качество и баланс',
    description: 'WQI, превышение ПДК, баланс водопотребления.',
    blocks: [
      {
        title: 'Качество воды',
        type: 'kpi',
        indicators: [
          'Индекс качества воды',
          'Превышение ПДК',
          'Дефицит / профицит воды',
        ],
      },
      {
        title: 'Тренды качества и водный баланс',
        type: 'placeholder',
        description: 'Динамика WQI/ПДК р. Есиль (2020–2025) и сезонный водный баланс — в детальном дашборде.',
        vizHint: 'Открыть «Качество и баланс» → /dashboard/water/quality',
        missingReason: 'Ряды WQI/ПДК и водного баланса ещё не загружены в indicator_values.',
      },
    ],
  },
]

export default function WaterDashboardPage() {
  return (
    <DirectionDashboard
      directionCode="water"
      directionLabel="Водные ресурсы"
      tabs={TABS}
    />
  )
}
