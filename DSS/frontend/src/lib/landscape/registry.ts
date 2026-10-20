// Реестр ландшафтного планирования: 13 фиксированных входов, 3 варианта
// зонирования и промежуточные расчётные слои.
//
// Источник — ТЗ «Требования к странице Ландшафтное планирование DSS» (28.09.2026)
// и техприложение «Технические данные 13 входных слоев.xlsx». slot_code жёстко
// закреплён за строкой интерфейса; group_id/dataset_id здесь намеренно не хранятся —
// актуальный latest.id всегда берётся из GET /landscape/slots (после замены входа
// dataset получает новый UUID). Английские title из GIS в интерфейсе не используются.

export type LandscapeBlock = 'Z' | 'U' | 'P'

export interface LandscapeSlotDef {
  code: string
  block: LandscapeBlock
  /** Короткое название в UI. Полное утверждённое имя датасета подставляет бэкенд. */
  label: string
}

export const LANDSCAPE_BLOCKS: Array<{ block: LandscapeBlock; title: string }> = [
  { block: 'Z', title: 'Природная значимость Z' },
  { block: 'U', title: 'Уязвимость U' },
  { block: 'P', title: 'Хозяйственная пригодность P' },
]

export const LANDSCAPE_SLOTS: LandscapeSlotDef[] = [
  { block: 'Z', code: 'lp-z-biodiversity', label: 'Биоразнообразие' },
  { block: 'Z', code: 'lp-z-lcr', label: 'Климатическое регулирование' },
  { block: 'Z', code: 'lp-z-soil-protection', label: 'Почвозащитная значимость' },
  { block: 'Z', code: 'lp-z-wr', label: 'Водорегулирующая значимость' },
  { block: 'U', code: 'lp-u-surf-water', label: 'Поверхностные воды' },
  { block: 'U', code: 'lp-u-soil', label: 'Почвы' },
  { block: 'U', code: 'lp-u-crop', label: 'Посевы' },
  { block: 'U', code: 'lp-u-veg', label: 'Естественная растительность' },
  { block: 'P', code: 'lp-p-irrigation', label: 'Орошаемое земледелие' },
  { block: 'P', code: 'lp-p-snow', label: 'Накопление снеготалых вод' },
  { block: 'P', code: 'lp-p-crop', label: 'Богарное земледелие' },
  { block: 'P', code: 'lp-p-pasture', label: 'Пастбища' },
  { block: 'P', code: 'lp-p-tourism', label: 'Рекреация и туризм' },
]

export interface LandscapeVariantDef {
  group: string
  short: string
  label: string
  hint: string
}

export const LANDSCAPE_VARIANTS: LandscapeVariantDef[] = [
  {
    group: 'lp-v1-conservative',
    short: 'V1',
    label: 'V1 — природоохранный',
    hint: 'Приоритет сохранения природных территорий и экосистемных функций',
  },
  {
    group: 'lp-v2-balanced',
    short: 'V2',
    label: 'V2 — сбалансированный',
    hint: 'Сбалансированное использование территории с учётом экологических ограничений',
  },
  {
    group: 'lp-v3-development',
    short: 'V3',
    label: 'V3 — хозяйственно-ориентированный',
    hint: 'Приоритет развития хозяйственной деятельности с учётом допустимых ограничений',
  },
]

/** Стартовый вариант интерфейса — значение по умолчанию, а не оценка «лучшего». */
export const DEFAULT_VARIANT = 'lp-v2-balanced'

/** Промежуточные расчётные слои (ТЗ §6.1, необязательная функция). */
export const LANDSCAPE_INTERMEDIATE: Array<{ group: string; label: string }> = [
  { group: 'lp-z-integrated', label: 'Интегральная природная значимость' },
  { group: 'lp-u-integrated', label: 'Интегральная уязвимость' },
  { group: 'lp-z-max', label: 'Максимальная природная значимость' },
  { group: 'lp-u-max', label: 'Максимальная уязвимость' },
  { group: 'lp-e', label: 'Экологическое ограничение' },
  { group: 'lp-c-irrigation', label: 'Конфликт: орошаемое земледелие' },
  { group: 'lp-c-snow', label: 'Конфликт: накопление снеготалых вод' },
  { group: 'lp-c-crop', label: 'Конфликт: богарное земледелие' },
  { group: 'lp-c-pasture', label: 'Конфликт: пастбища' },
  { group: 'lp-c-tourism', label: 'Конфликт: рекреация и туризм' },
  { group: 'lp-p-max', label: 'Максимальная хозяйственная пригодность' },
  { group: 'lp-c-max', label: 'Максимальный потенциальный конфликт' },
]

/**
 * Шкала для слоёв без SLD (входы и промежуточные): порядковая, светлый → тёмный
 * по возрастанию балла. Цвет — по смыслу блока: значимость — зелёный,
 * уязвимость/ограничение/конфликт — красный, пригодность — синий.
 */
export function rampForCode(code: string): string[] {
  if (code.startsWith('lp-z')) return ['#f7fcb9', '#addd8e', '#41ab5d', '#238443', '#005a32']
  if (code.startsWith('lp-p')) return ['#eff3ff', '#bdd7e7', '#6baed6', '#3182bd', '#08519c']
  return ['#fff5eb', '#fdd0a2', '#fd8d3c', '#d94801', '#8c2d04']
}

export function slotDef(code: string): LandscapeSlotDef | undefined {
  return LANDSCAPE_SLOTS.find((s) => s.code === code)
}

export function resultLabel(group: string): string {
  return (
    LANDSCAPE_VARIANTS.find((v) => v.group === group)?.label ??
    LANDSCAPE_INTERMEDIATE.find((l) => l.group === group)?.label ??
    group
  )
}

/**
 * Узлы серверного графа расчёта (для статуса запуска и приёмочной проверки).
 * V1–V3 считает узел 30 из E и исходных P1..P5 — не из P Max и не из C Max.
 */
export const LANDSCAPE_NODES: Record<string, string> = {
  '00': 'Интегральная природная значимость',
  '01': 'Интегральная уязвимость',
  '02': 'Максимальная природная значимость',
  '03': 'Максимальная уязвимость',
  '10': 'Экологическое ограничение',
  '20': 'Конфликты (5 слоёв)',
  '21': 'Максимальная хозяйственная пригодность',
  '30': 'Варианты V1–V3',
  '31': 'Максимальный конфликт',
}

/** Подпись группы (вход или результат) по её коду. */
export function groupLabel(group: string): string {
  return slotDef(group)?.label ?? resultLabel(group)
}
