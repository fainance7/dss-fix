// Цветовые шкалы для дашбордов направления «Водные ресурсы».
//
// Балльные шкалы оценки водообеспеченности (0–5) заданы заказчиком в
// docs/dashboards/water (дашборды Д9 «УДВn» и Д10 «УДВ(S)»). Хранятся как
// frontend-константа по тому же решению, что и климатические шкалы
// (см. lib/climate/colorScales.ts). Бин полуинтервальный: значение попадает в
// бин, если (min === null || value > min) && (max === null || value <= max).
// null = ±∞. Бины упорядочены от меньшего к большему.
//
// Палитра и типы переиспользуются из климатического модуля, чтобы легенды и
// раскраска карт выглядели единообразно во всех направлениях.

import {
  CLIMATE_COLORS,
  CLIMATE_NO_DATA_COLOR,
  type ColorBin,
  type ColorScale,
} from '@/lib/climate/colorScales'

// Чем выше водообеспеченность — тем «холоднее»/синее цвет (вода = благо):
// оранжевый (катастрофически низкая) → синий (очень высокая).

// 1. УДВn — удельная водообеспеченность населения, тыс.м³/чел (балл 0–5).
//    Пороги выведены из баллов таблицы Д9; приближённые, уточнить после
//    загрузки реальных данных через intake-portal.
const waterSupplyPopulation: ColorScale = {
  key: 'waterSupplyPopulation',
  name: 'Удельная водообеспеченность населения (УДВn)',
  unit: 'тыс.м³/чел',
  bins: [
    { min: null, max: 1, color: CLIMATE_COLORS.orange, label: '≤ 1 — балл 0 (катастрофически низкая)' },
    { min: 1, max: 5, color: CLIMATE_COLORS.yellow, label: '1–5 — балл 2 (низкая)' },
    { min: 5, max: 10, color: CLIMATE_COLORS.green, label: '5–10 — балл 3 (средняя)' },
    { min: 10, max: 30, color: CLIMATE_COLORS.lightBlue, label: '10–30 — балл 4 (высокая)' },
    { min: 30, max: null, color: CLIMATE_COLORS.blue, label: '> 30 — балл 5 (очень высокая)' },
  ],
}

// 2. УДВ(S) — удельная водообеспеченность на единицу площади, тыс.м³/км² (балл 0–5).
//    Пороги выведены из баллов таблицы Д10; приближённые.
const waterSupplyArea: ColorScale = {
  key: 'waterSupplyArea',
  name: 'Удельная водообеспеченность по площади (УДВ(S))',
  unit: 'тыс.м³/км²',
  bins: [
    { min: null, max: 5, color: CLIMATE_COLORS.orange, label: '≤ 5 — балл 0 (катастрофически низкая)' },
    { min: 5, max: 10, color: CLIMATE_COLORS.yellow, label: '5–10 — балл 1 (очень низкая)' },
    { min: 10, max: 20, color: CLIMATE_COLORS.lightGreen, label: '10–20 — балл 2 (низкая)' },
    { min: 20, max: 40, color: CLIMATE_COLORS.green, label: '20–40 — балл 3 (средняя)' },
    { min: 40, max: 100, color: CLIMATE_COLORS.lightBlue, label: '40–100 — балл 4 (высокая)' },
    { min: 100, max: null, color: CLIMATE_COLORS.blue, label: '> 100 — балл 5 (очень высокая)' },
  ],
}

export const WATER_COLOR_SCALES = {
  waterSupplyPopulation,
  waterSupplyArea,
} as const

export type WaterScaleKey = keyof typeof WATER_COLOR_SCALES

/** Нейтральный цвет для значений вне шкалы / отсутствия данных. */
export const WATER_NO_DATA_COLOR = CLIMATE_NO_DATA_COLOR

export type { ColorBin }
