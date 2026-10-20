// Построение choropleth-раскраски для датасета GIS.
//
// У vector_with_kato датасетов геометрия — одни и те же полигоны районов, а значения
// показателя лежат прямо в свойствах фичи: properties.data = { "YYYY-01-01": value },
// properties.kato (код КАТО), properties.name (район). Поэтому раскраска строится прямо
// из geojson, без /observations и без джойна kato→region_id.
//
// Числовые значения → непрерывный рамп (белый → базовый цвет) по min/max.
// Категориальные (нечисловые value_text) → отдельный цвет на категорию.

import type { GeoJsonFeatureCollection } from '@/lib/api/backend'
import { CLIMATE_NO_DATA_COLOR } from '@/lib/climate/colorScales'

// Палитра для категориальной раскраски (когда значения — текст, а не числа).
const CATEGORICAL_PALETTE = [
  '#2563eb', '#16a34a', '#f97316', '#9333ea', '#dc2626',
  '#0891b2', '#ca8a04', '#db2777', '#65a30d', '#475569',
]

type FeatureData = Record<string, number | string | null>

function featureData(f: { properties?: Record<string, unknown> | null }): FeatureData | null {
  const d = f.properties?.data
  return d && typeof d === 'object' ? (d as FeatureData) : null
}

/** Есть ли у фич свойство data — т.е. это датасет с районными значениями. */
export function hasChoroplethData(fc: GeoJsonFeatureCollection | null | undefined): boolean {
  if (!fc?.features?.length) return false
  return fc.features.some((f) => featureData(f) !== null)
}

/** Отсортированный список дат (ключей data) по всем фичам. */
export function datasetDates(fc: GeoJsonFeatureCollection | null | undefined): string[] {
  if (!fc?.features?.length) return []
  const set = new Set<string>()
  for (const f of fc.features) {
    const d = featureData(f)
    if (d) for (const k of Object.keys(d)) set.add(k)
  }
  return Array.from(set).sort()
}

/** Линейная интерполяция белый → hex-цвет по t∈[0,1]. Порт blendColor из IndicatorView. */
function rampColor(hex: string, t: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  const mix = (c: number) => Math.round(255 - t * (255 - c))
  return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`
}

const numberFmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })

export interface ChoroplethLegend {
  kind: 'continuous' | 'categorical'
  color: string
  min?: number
  max?: number
  minLabel?: string
  maxLabel?: string
  items?: { label: string; color: string }[]
}

export interface ChoroplethResult {
  /** Копия FeatureCollection с добавленными в properties полями __fill и __val. */
  fc: GeoJsonFeatureCollection
  legend: ChoroplethLegend | null
}

/**
 * Обогащает geojson полями __fill (цвет) и __val (подпись) по значению за дату `date`.
 * Возвращает новый FeatureCollection и описание легенды.
 */
export function buildChoropleth(
  fc: GeoJsonFeatureCollection,
  date: string,
  baseColor = '#2563eb',
): ChoroplethResult {
  // Значение каждой фичи за выбранную дату.
  const raw = fc.features.map((f) => {
    const d = featureData(f)
    const v = d ? d[date] : undefined
    return v === undefined ? null : v
  })

  const present = raw.filter((v): v is number | string => v !== null && v !== '')
  const allNumeric = present.length > 0 && present.every((v) => typeof v === 'number' && Number.isFinite(v))

  let legend: ChoroplethLegend | null = null
  let colorFor: (v: number | string | null) => string
  let labelFor: (v: number | string | null) => string | null

  if (allNumeric) {
    const nums = present as number[]
    const min = Math.min(...nums)
    const max = Math.max(...nums)
    colorFor = (v) => {
      if (v === null || typeof v !== 'number' || !Number.isFinite(v)) return CLIMATE_NO_DATA_COLOR
      const t = max === min ? 0.5 : (v - min) / (max - min)
      return rampColor(baseColor, 0.2 + t * 0.7)
    }
    labelFor = (v) => (typeof v === 'number' ? numberFmt.format(v) : null)
    legend = {
      kind: 'continuous',
      color: baseColor,
      min,
      max,
      minLabel: numberFmt.format(min),
      maxLabel: numberFmt.format(max),
    }
  } else {
    // Категориальная раскраска: цвет на уникальное текстовое значение.
    const cats = Array.from(new Set(present.map((v) => String(v))))
    const colorByCat = new Map<string, string>()
    cats.forEach((c, i) => colorByCat.set(c, CATEGORICAL_PALETTE[i % CATEGORICAL_PALETTE.length]))
    colorFor = (v) => (v === null || v === '' ? CLIMATE_NO_DATA_COLOR : colorByCat.get(String(v)) ?? CLIMATE_NO_DATA_COLOR)
    labelFor = (v) => (v === null || v === '' ? null : String(v))
    legend = {
      kind: 'categorical',
      color: baseColor,
      items: cats.map((c) => ({ label: c, color: colorByCat.get(c)! })),
    }
  }

  const features = fc.features.map((f, i) => ({
    ...f,
    properties: {
      ...(f.properties ?? {}),
      __fill: colorFor(raw[i]),
      __val: labelFor(raw[i]),
    },
  }))

  return { fc: { type: 'FeatureCollection', features }, legend }
}
