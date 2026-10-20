// Линия тренда для климатических графиков (метод наименьших квадратов).
// Чистая модульная функция: по точкам ряда (год→значение) считает линейную
// регрессию y = slope·x + intercept, где x — порядковый индекс точки (0..n-1).
// Используется в IndicatorView для пунктирной линии тренда на «Динамике по годам».

export interface TrendLine {
  slope: number
  intercept: number
  /** Значение тренда в первой точке (x=0). */
  yStart: number
  /** Значение тренда в последней точке (x=n-1). */
  yEnd: number
}

/**
 * Линейная регрессия по значениям ряда. x = индекс точки (равномерный шаг),
 * что корректно для годовых рядов без пропусков лет на оси. Возвращает null,
 * если точек меньше двух или все x совпадают (наклон не определён).
 */
export function linearTrend(values: number[]): TrendLine | null {
  const n = values.length
  if (n < 2) return null

  let sumX = 0
  let sumY = 0
  let sumXY = 0
  let sumXX = 0
  for (let i = 0; i < n; i++) {
    sumX += i
    sumY += values[i]
    sumXY += i * values[i]
    sumXX += i * i
  }
  const denom = n * sumXX - sumX * sumX
  if (denom === 0) return null

  const slope = (n * sumXY - sumX * sumY) / denom
  const intercept = (sumY - slope * sumX) / n
  return {
    slope,
    intercept,
    yStart: intercept,
    yEnd: slope * (n - 1) + intercept,
  }
}
