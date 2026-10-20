'use client'

import ClimateSectionPage, { type ClimateTab } from '@/components/dashboard/climate/ClimateSectionPage'
import MonthlyClimateCard from '@/components/dashboard/MonthlyClimateCard'
import {
  ActiveSumsTable,
  ProbabilityTable,
  TransitionsTable,
} from '@/components/dashboard/climate/ClimateReferenceTables'
import { NORM_PERIOD_LABEL } from '@/lib/climate/transforms'

/**
 * Климат — «Термические ресурсы» (предложения заказчика по климатическому разделу).
 * Порядок подразделов: средняя годовая температура → средняя месячная температура →
 * даты перехода через 5/10/15 °C → ΣT(5-8) > 10 °C по годам и отклонение от нормы →
 * суммы активных температур выше 5/10/15 °C → обеспеченность ΣT > 10 °C →
 * продолжительность вегетационного периода.
 * Среднегодовая температура и ΣT(5-8) — по метеостанциям (среднее станций района);
 * многолетние таблицы — из документа заказчика (2003–2024 гг.).
 */

const SKO = 'по районам Северо-Казахстанской области'
const T_ANNUAL = 'Среднегодовая температура воздуха'
const SUM_T = 'ΣT(5-8)'
const DURATION = 'Продолжительность вегетационного периода'

const TABS: ClimateTab[] = [
  {
    id: 'annual',
    title: 'Средняя годовая температура',
    description:
      'Средняя годовая температура воздуха — среднее двенадцати месячных значений, рассчитанных из декадных данных метеостанций; значение района — среднее станций района.',
    panels: [
      {
        title: `Средняя годовая температура воздуха ${SKO}, °C, {year} г.`,
        name: 'Средняя годовая температура воздуха',
        indicator: T_ANNUAL,
        unit: '°C',
        color: '#dc2626',
        decimals: 1,
        dynamics: 'Динамика средней годовой температуры воздуха {region}, {years}',
        ranking: 'Ранжирование районов по средней годовой температуре воздуха, °C, {year} г.',
      },
      {
        title: `Аномалия средней годовой температуры воздуха в {year} г. от среднемноголетнего значения (${NORM_PERIOD_LABEL}), °C`,
        name: 'Аномалия средней годовой температуры воздуха',
        indicator: T_ANNUAL,
        unit: '°C',
        color: '#dc2626',
        decimals: 1,
        transform: 'deviationNorm',
        heatmap: 'tempDeviation',
        dynamics: 'Динамика аномалии средней годовой температуры воздуха {region}, {years}',
        ranking: 'Ранжирование районов по аномалии средней годовой температуры воздуха, °C, {year} г.',
      },
    ],
  },
  {
    id: 'monthly',
    title: 'Средняя месячная температура',
    description:
      'Средняя месячная температура воздуха по месяцам за 2003–2025 гг. — по области, району или метеостанции. Месяц рассчитан из трёх декад как среднее, взвешенное по числу дней.',
    before: (
      <MonthlyClimateCard
        title="Средняя месячная температура воздуха, °C"
        indicator="temperature"
        description={`В сравнении со среднемноголетним значением того же месяца (${NORM_PERIOD_LABEL}).`}
      />
    ),
    panels: [],
  },
  {
    id: 'transitions',
    title: 'Даты перехода через 5, 10 и 15 °C',
    panels: [],
    after: <TransitionsTable />,
  },
  {
    id: 'sumt',
    title: 'Сумма активных температур ΣT(5-8)',
    description:
      'Сумма активных температур воздуха выше 10 °C за период активной вегетации сельскохозяйственных культур (май–август), ΣT(5-8). Каждый район в каждом году сравнивается со своим средним многолетним значением: отклонение в °C·сут и в % от нормы.',
    panels: [
      {
        title: `Сумма активных температур воздуха выше 10 °C за май–август ΣT(5-8) ${SKO}, °C·сут, {year} г.`,
        name: 'Сумма активных температур выше 10 °C ΣT(5-8)',
        indicator: SUM_T,
        unit: '°C·сут',
        color: '#ea580c',
        decimals: 1,
        dynamics: 'Динамика суммы активных температур воздуха выше 10 °C ΣT(5-8) {region}, {years}',
        ranking: 'Ранжирование районов по сумме активных температур выше 10 °C ΣT(5-8), °C·сут, {year} г.',
      },
      {
        title: `Отклонение суммы активных температур выше 10 °C ΣT(5-8) в {year} г. от средних многолетних значений (${NORM_PERIOD_LABEL}), °C·сут`,
        name: 'Отклонение ΣT(5-8) от нормы',
        indicator: SUM_T,
        unit: '°C·сут',
        color: '#ea580c',
        decimals: 1,
        transform: 'deviationNorm',
        dynamics: 'Динамика отклонения суммы активных температур выше 10 °C ΣT(5-8) от нормы {region}, {years}',
        ranking: 'Ранжирование районов по отклонению ΣT(5-8) от нормы, °C·сут, {year} г.',
      },
      {
        title: `Отклонение суммы активных температур выше 10 °C ΣT(5-8) в {year} г. от средних многолетних значений (${NORM_PERIOD_LABEL}), %`,
        name: 'Отклонение ΣT(5-8) от нормы, %',
        indicator: SUM_T,
        unit: '%',
        color: '#ea580c',
        decimals: 1,
        transform: 'deviationNormPct',
        heatmap: 'sumTDeviationPct',
        dynamics: 'Динамика суммы активных температур выше 10 °C ΣT(5-8) относительно нормы {region}, {years}, %',
        ranking: 'Ранжирование районов по отклонению ΣT(5-8) от нормы, %, {year} г.',
      },
    ],
  },
  {
    id: 'sums',
    title: 'Теплообеспеченность',
    panels: [],
    after: <ActiveSumsTable />,
  },
  {
    id: 'probability',
    title: 'Обеспеченность теплом (P, %)',
    panels: [],
    after: <ProbabilityTable />,
  },
  {
    id: 'duration',
    title: 'Продолжительность вегетационного периода',
    description: 'Продолжительность периода с температурой воздуха выше 10 °C, сутки.',
    panels: [
      {
        title: `Продолжительность вегетационного периода ${SKO}, сут., {year} г.`,
        name: 'Продолжительность вегетационного периода',
        indicator: DURATION,
        unit: 'сутки',
        color: '#16a34a',
        decimals: 0,
        dynamics: 'Динамика продолжительности вегетационного периода {region}, {years}',
        ranking: 'Ранжирование районов по продолжительности вегетационного периода, сут., {year} г.',
      },
    ],
  },
]

export default function ClimateThermalDashboardPage() {
  return (
    <ClimateSectionPage
      title="Термические ресурсы"
      tabs={TABS}
      summaryTitle="Климат — термические ресурсы"
      kpi={(resolve) => {
        const t = resolve(T_ANNUAL)?.id ?? null
        const sum = resolve(SUM_T)?.id ?? null
        const dur = resolve(DURATION)?.id ?? null
        return [
          { label: 'Средняя годовая температура воздуха по области', indicatorId: t, agg: 'avg', unit: '°C', decimals: 1 },
          { label: 'Средняя сумма активных температур выше 10 °C ΣT(5-8) по области', indicatorId: sum, agg: 'avg', unit: '°C·сут', decimals: 1 },
          { label: 'Район с максимальной суммой активных температур ΣT(5-8)', indicatorId: sum, agg: 'argmax' },
          { label: 'Район с минимальной суммой активных температур ΣT(5-8)', indicatorId: sum, agg: 'argmin' },
          { label: 'Средняя продолжительность вегетационного периода', indicatorId: dur, agg: 'avg', unit: 'сутки', decimals: 0 },
        ]
      }}
    />
  )
}
