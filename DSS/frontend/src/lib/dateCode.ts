// Декодирование показателей-дат, хранящихся в формате ДД.ММ как число.
//
// Часть климатических показателей («Дата перехода температуры через …°C»)
// загружена в indicator_values в виде десятичного кода ДД.ММ, где целая часть —
// день, а дробная — месяц: напр. 6.11 = 6 ноября, 12.04 = 12 апреля,
// 23.1 = 23 октября (хвостовой ноль теряется при хранении double, поэтому .1 → 10).
//
// Такие значения НЕЛЬЗЯ усреднять как числа (среднее «дней.месяцев» — не дата),
// поэтому KPI-карточки используют эти утилиты: декодируют код в день/месяц,
// считают «типичную» дату (медиану по дню года) и диапазон.

/** Единица показателей-дат в справочнике. */
const DATE_UNIT = 'дата'

const MONTHS_RU_GEN = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
] as const

// Кумулятивные дни на начало месяца (невисокосный год); индекс 12 = 365.
const CUM_DAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365]

/** Показатель кодирует дату форматом ДД.ММ (по единице измерения). */
export function isDateCodeUnit(unit?: string | null): boolean {
  return (unit ?? '').trim().toLowerCase() === DATE_UNIT
}

/** Декодирует код ДД.ММ в {day, month}; null — если значение некорректно. */
export function decodeDateCode(value: number): { day: number; month: number } | null {
  if (!Number.isFinite(value) || value <= 0) return null
  const day = Math.floor(value)
  // round() гасит ошибку float: 6.11→0.10999…→11, 23.1→0.0999…→10.
  const month = Math.round((value - day) * 100)
  if (day < 1 || day > 31 || month < 1 || month > 12) return null
  return { day, month }
}

/** День года (1..365) для сравнения/усреднения дат без привязки к году. */
function dayOfYear(day: number, month: number): number {
  return CUM_DAYS[month - 1] + day
}

/** Обратное преобразование дня года в {day, month}. */
function fromDayOfYear(doy: number): { day: number; month: number } {
  const clamped = Math.min(365, Math.max(1, Math.round(doy)))
  let m = 1
  while (m < 12 && clamped > CUM_DAYS[m]) m += 1
  return { day: clamped - CUM_DAYS[m - 1], month: m }
}

/** «6 ноября» из {day, month}. */
function formatDM(d: { day: number; month: number }): string {
  return `${d.day} ${MONTHS_RU_GEN[d.month - 1]}`
}

/** «6 ноября» из кода ДД.ММ; null — если не декодируется. */
export function formatDateCode(value: number): string | null {
  const d = decodeDateCode(value)
  return d ? formatDM(d) : null
}

/**
 * Сводка по набору кодов ДД.ММ за один период: типичная (медианная) дата и
 * диапазон min–max. Диапазон сворачивается, если месяц общий («6–10 ноября»)
 * или дата единственная.
 */
export function summarizeDateCodes(
  values: number[],
): { typical: string; range: string | null } | null {
  const decoded = values
    .map(decodeDateCode)
    .filter((d): d is { day: number; month: number } => d !== null)
  if (decoded.length === 0) return null

  const doys = decoded.map((d) => dayOfYear(d.day, d.month)).sort((a, b) => a - b)
  const median = doys[Math.floor((doys.length - 1) / 2)]
  const typical = formatDM(fromDayOfYear(median))

  const lo = fromDayOfYear(doys[0])
  const hi = fromDayOfYear(doys[doys.length - 1])
  if (doys[0] === doys[doys.length - 1]) return { typical, range: null }
  const range =
    lo.month === hi.month
      ? `${lo.day}–${hi.day} ${MONTHS_RU_GEN[hi.month - 1]}`
      : `${formatDM(lo)} – ${formatDM(hi)}`
  return { typical, range }
}
