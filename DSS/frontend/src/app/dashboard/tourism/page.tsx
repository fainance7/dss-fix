'use client'

import DirectionDashboard, {
  type DashboardTab,
} from '@/components/dashboard/DirectionDashboard'

const TABS: DashboardTab[] = [
  {
    id: 'summary',
    title: 'Сводные показатели',
    description: 'Туристский поток, доход, инфраструктура размещения 2020–2025.',
    blocks: [
      {
        title: 'KPI туризма',
        type: 'kpi',
        indicators: [
          'Численность туристов',
          'Доход от туристской деятельности',
          'Количество средств размещения',
          'Ёмкость номерного фонда',
          'Коэффициент загрузки номерного фонда',
        ],
      },
      {
        title: 'Динамика турпотока и дохода',
        type: 'line',
        indicators: ['Численность туристов', 'Доход от туристской деятельности'],
      },
      {
        title: 'Инфраструктура размещения',
        type: 'kpi',
        indicators: [
          'Единовременная вместимость',
          'Количество номеров',
          'Предоставлено койко-суток',
          'Количество средств размещения',
        ],
      },
    ],
  },
  {
    id: 'flow',
    title: 'Структура потока',
    description: 'Доли резидентов / нерезидентов / иностранных туристов.',
    blocks: [
      {
        title: 'Структура турпотока',
        type: 'kpi',
        indicators: [
          'Доля туристов-резидентов',
          'Доля иностранных туристов',
        ],
      },
      {
        title: 'Структура турпотока: резиденты и иностранцы',
        type: 'pie',
        indicators: ['Доля туристов-резидентов', 'Доля иностранных туристов'],
        pieOblast: true,
        pieComplementLabel: 'Прочие / нерезиденты',
      },
    ],
  },
  {
    id: 'geography',
    title: 'География и инфраструктура',
    description: 'Распределение турпотока по 14 районам СКО + Имантау-Шалкарская курортная зона.',
    blocks: [
      {
        title: 'Картограмма потока по районам',
        type: 'placeholder',
        description: 'Choropleth с категорией ТРП (1–4) по районам.',
        vizHint: 'Карта направлений: /dashboard/direction-map?direction=tourism',
        missingReason: 'Нет районных значений туристского потока (данные только по области).',
      },
      {
        title: 'Распределение по районам (таблица)',
        type: 'table',
        indicators: ['Распределение туристского потока', 'Категория'],
      },
      {
        title: 'Рекреационный потенциал территории',
        type: 'geo',
        description: 'Пригодность территории для рекреации и туризма, озёра и места встреч редких животных.',
        geoLayers: ['suit-tourism', 'lakes', 'rivers', 'animals-rare'],
        geoInitial: ['suit-tourism', 'lakes'],
      },
    ],
  },
  {
    id: 'potential',
    title: 'Потенциал развития',
    description: 'Категории туристско-рекреационного потенциала районов.',
    blocks: [
      {
        title: 'Объекты туристской базы',
        type: 'kpi',
        indicators: [
          'Объекты культурно-исторического наследия',
          'Объекты питания',
          'Лечебно-оздоровительные учреждения',
        ],
      },
    ],
  },
]

export default function TourismDashboardPage() {
  return (
    <DirectionDashboard
      directionCode="tourism"
      directionLabel="Туризм"
      tabs={TABS}
    />
  )
}
