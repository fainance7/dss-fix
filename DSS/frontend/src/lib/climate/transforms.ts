// Производные преобразования рядов показателей для климатических карт.
// Стабильные модульные функции (используются как valueTransform в IndicatorView,
// чтобы карта/диаграммы показывали производную величину, а не сырое значение).

import type { IndicatorValuesListItem } from '@/lib/api/backend'

/**
 * Отклонение от среднеобластного: значение района минус среднее по всем районам
 * за тот же период. Отрицательное — ниже области, положительное — выше.
 */
export function deviationFromOblastMean(
  items: IndicatorValuesListItem[],
): IndicatorValuesListItem[] {
  const acc = new Map<string, { sum: number; n: number }>()
  for (const it of items) {
    const a = acc.get(it.period_start) ?? { sum: 0, n: 0 }
    a.sum += it.value
    a.n += 1
    acc.set(it.period_start, a)
  }
  const meanByPeriod = new Map<string, number>()
  for (const [p, a] of acc) meanByPeriod.set(p, a.n ? a.sum / a.n : 0)
  return items.map((it) => ({
    ...it,
    value: it.value - (meanByPeriod.get(it.period_start) ?? it.value),
  }))
}

/** Базовый период климатической нормы дашбордов (решение заказчика). */
export const NORM_PERIOD: [number, number] = [2003, 2025]
export const NORM_PERIOD_LABEL = `${NORM_PERIOD[0]}–${NORM_PERIOD[1]} гг.`

/** Многолетняя норма по районам = среднее значений района за базовый период. */
function normByRegion(items: IndicatorValuesListItem[]): Map<string, number> {
  const acc = new Map<string, { sum: number; n: number }>()
  for (const it of items) {
    const y = Number(it.period_start.slice(0, 4))
    if (y < NORM_PERIOD[0] || y > NORM_PERIOD[1]) continue
    const a = acc.get(it.region_id) ?? { sum: 0, n: 0 }
    a.sum += it.value
    a.n += 1
    acc.set(it.region_id, a)
  }
  const norm = new Map<string, number>()
  for (const [r, a] of acc) norm.set(r, a.n ? a.sum / a.n : 0)
  return norm
}

/** Процент от многолетней нормы района: value / норма(район) × 100. Районы без нормы отбрасываются. */
export function pctOfNorm(items: IndicatorValuesListItem[]): IndicatorValuesListItem[] {
  const norm = normByRegion(items)
  return items.flatMap((it) => {
    const n = norm.get(it.region_id)
    return n ? [{ ...it, value: (it.value / n) * 100 }] : []
  })
}

/** Отклонение от многолетней нормы района: value − норма(район). */
export function deviationFromNorm(items: IndicatorValuesListItem[]): IndicatorValuesListItem[] {
  const norm = normByRegion(items)
  return items.flatMap((it) => {
    const n = norm.get(it.region_id)
    return n !== undefined ? [{ ...it, value: it.value - n }] : []
  })
}

/** Отклонение от многолетней нормы района в процентах: (value / норма − 1) × 100. */
export function deviationFromNormPct(items: IndicatorValuesListItem[]): IndicatorValuesListItem[] {
  return pctOfNorm(items).map((it) => ({ ...it, value: it.value - 100 }))
}

/**
 * Отношение основного показателя к вторичному по совпадающим (район, период).
 * Пары без вторичного значения или с нулевым знаменателем отбрасываются.
 */
export function ratioToSecondary(
  primary: IndicatorValuesListItem[],
  secondary: IndicatorValuesListItem[],
): IndicatorValuesListItem[] {
  const key = (regionId: string, period: string) => `${regionId}|${period}`
  const sec = new Map<string, number>()
  for (const it of secondary) sec.set(key(it.region_id, it.period_start), it.value)
  const out: IndicatorValuesListItem[] = []
  for (const it of primary) {
    const denom = sec.get(key(it.region_id, it.period_start))
    if (denom === undefined || denom === 0) continue
    out.push({ ...it, value: it.value / denom })
  }
  return out
}
