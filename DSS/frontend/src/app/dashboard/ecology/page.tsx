'use client'

import DirectionDashboard, {
  type DashboardTab,
} from '@/components/dashboard/DirectionDashboard'

const TABS: DashboardTab[] = [
  {
    id: 'kpi',
    title: 'KPI экологии',
    description: 'Сводные показатели загрязнения, ООПТ, отходов.',
    blocks: [
      {
        title: 'Главные индикаторы',
        type: 'kpi',
        indicators: [
          'Индекс загрязнения атмосферного воздуха',
          'Выбросы загрязняющих веществ',
          'Доля особо охраняемых природных территорий',
          'Образование отходов на 1 жителя',
        ],
      },
    ],
  },
  {
    id: 'air',
    title: 'Состояние воздуха',
    description: 'Объём выбросов и распределение по источникам.',
    blocks: [
      {
        title: 'Динамика выбросов',
        type: 'line',
        indicators: ['Выбросы загрязняющих веществ', 'Выбросы на 1 жителя'],
      },
      {
        title: 'Распределение выбросов по источникам',
        type: 'placeholder',
        description: 'Промышленность / Транспорт / Энергетика.',
        vizHint: 'Pie chart',
        missingReason: 'Нет разбивки выбросов по источникам за общий год.',
      },
      {
        title: 'Спутниковый мониторинг атмосферы, 2025',
        type: 'geo',
        description: 'Средние за 2025 год значения по спутниковым данным. Выберите вещество.',
        geoLayers: ['air-no2', 'air-so2', 'air-co', 'air-o3', 'air-ch4', 'air-aer'],
      },
    ],
  },
  {
    id: 'water',
    title: 'Состояние воды',
    description: 'Качество поверхностных вод по р. Ишим и притокам.',
    blocks: [
      {
        title: 'Качество воды',
        type: 'kpi',
        indicators: ['Качество поверхностных вод'],
      },
      {
        title: 'WQI и превышение ПДК',
        type: 'placeholder',
        description: 'Бассейн р. Ишим — 78 притоков.',
        vizHint: 'Choropleth + line chart',
        missingReason: 'Ряд WQI по створам ещё не загружен в indicator_values.',
      },
    ],
  },
  {
    id: 'land',
    title: 'Земельные ресурсы',
    description: 'Нарушенные / рекультивированные земли.',
    blocks: [
      {
        title: 'Площадь нарушенных земель',
        type: 'kpi',
        indicators: ['Площадь нарушенных земель', 'Рекультивация земель'],
      },
    ],
  },
  {
    id: 'oopt',
    title: 'ООПТ и биоразнообразие',
    description: '17 объектов ООПТ республиканского значения.',
    blocks: [
      {
        title: 'ООПТ',
        type: 'kpi',
        indicators: ['Площадь особо охраняемых природных территорий', 'Доля особо охраняемых'],
      },
      {
        title: 'Карта ООПТ и биоразнообразия',
        type: 'geo',
        description:
          'Значимость территории для поддержания биоразнообразия, особо охраняемые природные территории и места встреч животных.',
        geoLayers: ['biodiversity', 'oopt', 'animals-rare', 'animals-game'],
        geoInitial: ['biodiversity', 'animals-rare'],
      },
    ],
  },
  {
    id: 'waste',
    title: 'Отходы и нагрузка',
    description: 'Образование, переработка, захоронение, экологический след.',
    blocks: [
      {
        title: 'Структура отходов',
        type: 'kpi',
        indicators: [
          'Образование отходов на 1 жителя',
          'Доля переработки',
          'Доля захоронения',
          'Экологический след',
          'Удельная биоёмкость на единицу площади',
        ],
      },
    ],
  },
]

export default function EcologyDashboardPage() {
  return (
    <DirectionDashboard
      directionCode="ecology"
      directionLabel="Экология"
      tabs={TABS}
    />
  )
}
