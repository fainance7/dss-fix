'use client'

/**
 * Общие элементы SVG-графиков дашбордов: «ровная» шкала, вертикальная ось с
 * делениями и подписью единицы измерения, горизонтальная ось значений для
 * линейчатых диаграмм, единый размер шрифта подписей.
 */

/** Размер шрифта подписей осей (в единицах viewBox графика). */
export const AXIS_FONT = 15
const AXIS_COLOR = '#94a3b8'
const GRID_COLOR = '#e5e7eb'
const LABEL_COLOR = '#6b7280'

/** Подпись оси для показателей без единицы измерения. */
export function unitLabel(unit?: string | null): string {
  return unit && unit.trim() ? unit.trim() : 'безразмерная величина'
}

export function formatValue(v: number): string {
  // ru-локаль: десятичный разделитель — запятая (замечание заказчика).
  // toFixed всегда даёт точку в числовой части (она идёт первой), поэтому
  // замена первой точки безопасна и не задевает суффикс «тыс.».
  return formatValueRaw(v).replace('.', ',')
}

function formatValueRaw(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(2)} млн`
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(1)} тыс.`
  if (Math.abs(v) >= 10) return v.toFixed(1)
  if (Math.abs(v) >= 1) return v.toFixed(2)
  return v.toFixed(3)
}

/**
 * Число в русской записи без сокращений «тыс.»/«млн»: пробел между разрядами,
 * десятичная запятая, знак «−»
 * (климатические дашборды: два знака, термические ресурсы — один), ровно `decimals` знаков.
 */
export function formatNumber(v: number, decimals = 2): string {
  const s = Math.abs(v).toLocaleString('ru-RU', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
  const rounded = Number(Math.abs(v).toFixed(decimals))
  return v < 0 && rounded !== 0 ? `−${s}` : s
}

/** Форматтер значения: с заданной точностью — без сокращений, иначе formatValue. */
export function valueFormatter(decimals?: number): (v: number) => string {
  return decimals === undefined ? formatValue : (v: number) => formatNumber(v, decimals)
}

export interface Scale {
  min: number
  max: number
  step: number
  ticks: number[]
}

/** Шкала с «круглыми» делениями (шаг 1 / 2 / 2,5 / 5 × 10ⁿ), покрывающая [lo, hi]. */
export function niceScale(lo: number, hi: number, target = 5): Scale {
  let a = Number.isFinite(lo) ? lo : 0
  let b = Number.isFinite(hi) ? hi : 1
  if (a > b) [a, b] = [b, a]
  if (a === b) b = a + (Math.abs(a) || 1)
  const raw = (b - a) / target
  const mag = 10 ** Math.floor(Math.log10(raw))
  const norm = raw / mag
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag
  const fix = (v: number) => Number(v.toPrecision(12))
  const min = fix(Math.floor(a / step) * step)
  const max = fix(Math.ceil(b / step) * step)
  const ticks: number[] = []
  for (let v = min; v <= max + step / 2; v += step) ticks.push(fix(v))
  return { min, max, step, ticks }
}

/** Форматтер делений: один масштаб (тыс./млн) на всю ось, десятичная запятая. */
export function tickFormatter(scale: Scale): (v: number) => string {
  const maxAbs = Math.max(Math.abs(scale.min), Math.abs(scale.max))
  const [div, suffix] =
    maxAbs >= 1_000_000 ? [1_000_000, ' млн'] : maxAbs >= 10_000 ? [1_000, ' тыс.'] : [1, '']
  const step = scale.step / div
  const exp = Math.floor(Math.log10(step))
  const lead = Math.round(step / 10 ** exp)
  // Шаг 2,5·10ⁿ требует на один знак больше, чем 1/2/5·10ⁿ.
  const digits = Math.min(6, Math.max(0, -exp + (step / 10 ** exp - lead !== 0 ? 1 : 0)))
  return (v: number) =>
    (v / div).toLocaleString('ru-RU', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }) + (v !== 0 ? suffix : '')
}

/** Ширина подписей делений шкалы (оценка по числу символов). */
function tickLabelWidth(scale: Scale): number {
  const fmt = tickFormatter(scale)
  return Math.max(...scale.ticks.map((v) => fmt(v).length)) * AXIS_FONT * 0.58
}

/**
 * Отступ от края графика до вертикальной оси: ровно под подписи делений и,
 * если подпись единицы выводится вдоль оси, — под неё. Без пустого поля слева.
 */
export function yAxisPad(scale: Scale, withUnit: boolean): number {
  return Math.ceil(8 + tickLabelWidth(scale) + (withUnit ? 6 + AXIS_FONT + 4 : 4))
}

/**
 * Вертикальная шкала: линия оси, деления с подписями, линии сетки и (опц.)
 * подпись единицы измерения вдоль оси.
 */
export function YAxis({
  scale,
  x,
  top,
  height,
  gridTo,
  unit,
  unitX,
  side = 'left',
  color,
}: {
  scale: Scale
  /** X-координата линии оси. */
  x: number
  top: number
  height: number
  /** Если задано — от каждого деления до этой X-координаты рисуется линия сетки. */
  gridTo?: number
  /** Подпись вдоль оси (единица измерения). undefined — не выводить. */
  unit?: string
  /** X-координата подписи единицы (по умолчанию — вплотную к подписям делений). */
  unitX?: number
  side?: 'left' | 'right'
  /** Цвет подписей (для второй оси — цвет её серии). */
  color?: string
}) {
  const fmt = tickFormatter(scale)
  const dir = side === 'left' ? -1 : 1
  const yOf = (v: number) => top + (1 - (v - scale.min) / (scale.max - scale.min)) * height
  // Подпись единицы — вплотную к подписям делений. Ширину подписей оцениваем по
  // числу символов (средняя ширина цифры ≈ 0,58 кегля). Повёрнутый на −90° текст
  // растёт от базовой линии влево, поэтому справа от оси к ней прибавляется кегль.
  const labelW = tickLabelWidth(scale)
  const ux =
    unitX ??
    (side === 'left'
      ? Math.max(AXIS_FONT, x - 8 - labelW - 6)
      : x + 8 + labelW + 6 + AXIS_FONT)
  return (
    <g>
      {scale.ticks.map((v) => (
        <g key={v}>
          {gridTo !== undefined && (
            <line x1={x} x2={gridTo} y1={yOf(v)} y2={yOf(v)} stroke={GRID_COLOR} strokeDasharray="3,3" />
          )}
          <line x1={x} x2={x + dir * 5} y1={yOf(v)} y2={yOf(v)} stroke={AXIS_COLOR} />
          <text
            x={x + dir * 8}
            y={yOf(v) + 4}
            textAnchor={side === 'left' ? 'end' : 'start'}
            style={{ fontSize: AXIS_FONT, fill: color ?? LABEL_COLOR }}
          >
            {fmt(v)}
          </text>
        </g>
      ))}
      <line x1={x} x2={x} y1={top} y2={top + height} stroke={AXIS_COLOR} />
      {unit !== undefined && (
        <text
          x={ux}
          y={top + height / 2}
          textAnchor="middle"
          transform={`rotate(-90 ${ux} ${top + height / 2})`}
          style={{ fontSize: AXIS_FONT, fill: color ?? LABEL_COLOR }}
        >
          {unit}
        </text>
      )}
    </g>
  )
}

/**
 * Горизонтальная шкала значений для линейчатых (горизонтальных) диаграмм:
 * линия оси под рядами, деления с подписями, вертикальные линии сетки и
 * подпись единицы измерения.
 */
export function XValueAxis({
  scale,
  y,
  left,
  width,
  gridTop,
  unit,
}: {
  scale: Scale
  /** Y-координата линии оси. */
  y: number
  left: number
  width: number
  /** Если задано — от каждого деления вверх до этой Y-координаты рисуется сетка. */
  gridTop?: number
  unit?: string
}) {
  const fmt = tickFormatter(scale)
  const xOf = (v: number) => left + ((v - scale.min) / (scale.max - scale.min)) * width
  return (
    <g>
      {scale.ticks.map((v) => (
        <g key={v}>
          {gridTop !== undefined && (
            <line x1={xOf(v)} x2={xOf(v)} y1={gridTop} y2={y} stroke={GRID_COLOR} strokeDasharray="3,3" />
          )}
          <line x1={xOf(v)} x2={xOf(v)} y1={y} y2={y + 5} stroke={AXIS_COLOR} />
          <text x={xOf(v)} y={y + 21} textAnchor="middle" style={{ fontSize: AXIS_FONT, fill: LABEL_COLOR }}>
            {fmt(v)}
          </text>
        </g>
      ))}
      <line x1={left} x2={left + width} y1={y} y2={y} stroke={AXIS_COLOR} />
      {unit !== undefined && (
        <text
          x={left + width / 2}
          y={y + 42}
          textAnchor="middle"
          style={{ fontSize: AXIS_FONT, fill: LABEL_COLOR }}
        >
          {unit}
        </text>
      )}
    </g>
  )
}
