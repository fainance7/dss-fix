// Минимальный транслятор SLD/SE → стиль слоёв MapLibre.
//
// Поддерживается подмножество, которое реально экспортирует QGIS для
// категоризированной/градуированной символики (SLD 1.1 + SE 1.1, а также
// SLD 1.0 с CssParameter):
//   Rule → Filter (PropertyIsEqualTo/NotEqualTo/LessThan/GreaterThan/
//          LessThanOrEqualTo/GreaterThanOrEqualTo/Between/Null, And/Or/Not)
//          либо ElseFilter;
//   PolygonSymbolizer (Fill/Stroke), LineSymbolizer (Stroke),
//   PointSymbolizer (Graphic/Mark → circle).
//
// Каждое свойство отрисовки собирается в выражение MapLibre вида
// ['case', фильтр₁, значение₁, …, значение-ElseFilter]. Для взаимоисключающих
// категорий (обычный случай QGIS) это эквивалентно семантике SLD; если правила
// пересекаются, SLD рисует все совпавшие поверх друг друга, а 'case' берёт
// первое — расхождение возможно только на таких стилях.
//
// Всё, что не переносится (TextSymbolizer, ExternalGraphic, штриховки,
// PropertyIsLike, масштабные диапазоны), собирается в unsupported[] — чтобы
// на UI было видно, что стиль отрендерен не полностью.

/** Выражение MapLibre (структурно — вложенные массивы). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type MlExpr = any

export interface SldLegendItem {
  label: string
  color: string
  strokeColor?: string
  kind: 'polygon' | 'line' | 'point'
}

export interface SldStyle {
  /** se:Name слоя/стиля (для отладки). */
  name?: string
  /** Заливка полигонов. */
  fill?: { color: MlExpr; opacity: MlExpr }
  /** Контур полигонов (Stroke внутри PolygonSymbolizer). */
  outline?: { color: MlExpr; width: MlExpr }
  /** Линейные объекты (LineSymbolizer). */
  line?: { color: MlExpr; width: MlExpr }
  /** Точки (PointSymbolizer → circle-слой). */
  circle?: { color: MlExpr; radius: MlExpr; strokeColor: MlExpr; strokeWidth: MlExpr }
  /** Легенда в порядке правил SLD. */
  legend: SldLegendItem[]
  /** Атрибуты, по которым построены фильтры (для подсказки на карте). */
  propertyNames: string[]
  /** Что не удалось перенести. */
  unsupported: string[]
  ruleCount: number
}

// ── Обход DOM без учёта префиксов пространств имён ──────────────────────────

function childrenByName(el: Element | null, name: string): Element[] {
  if (!el) return []
  return Array.from(el.children).filter((c) => c.localName === name)
}

function childByName(el: Element | null, name: string): Element | null {
  return childrenByName(el, name)[0] ?? null
}

function descendantsByName(root: Element | Document, name: string): Element[] {
  const out: Element[] = []
  const walk = (el: Element) => {
    for (const c of Array.from(el.children)) {
      if (c.localName === name) out.push(c)
      walk(c)
    }
  }
  const start = root instanceof Document ? root.documentElement : root
  if (!start) return out
  if (start.localName === name) out.push(start)
  walk(start)
  return out
}

function text(el: Element | null): string {
  return el?.textContent ?? ''
}

/**
 * Параметры Fill/Stroke: SLD 1.1 отдаёт их как se:SvgParameter name="fill",
 * SLD 1.0 — как CssParameter. Читаем оба варианта.
 */
function svgParams(el: Element | null): Record<string, string> {
  if (!el) return {}
  const out: Record<string, string> = {}
  for (const c of Array.from(el.children)) {
    if (c.localName === 'SvgParameter' || c.localName === 'CssParameter') {
      const n = c.getAttribute('name')
      if (n) out[n] = text(c).trim()
    }
  }
  return out
}

// ── Фильтры ogc → выражения MapLibre ───────────────────────────────────────

function literalNumber(s: string): number | null {
  const t = s.trim().replace(',', '.')
  if (t === '') return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

/** ['get', prop] с числовым приведением для операций сравнения. */
function numericGet(prop: string): MlExpr {
  return ['to-number', ['get', prop]]
}

/**
 * Равенство: значение в GeoJSON может быть как строкой, так и числом
 * (QGIS пишет литерал строкой). Сравниваем с обоими вариантами, чтобы не
 * ловить ошибку приведения типов внутри выражения.
 */
function eqExpr(prop: string, literal: string): MlExpr {
  const num = literalNumber(literal)
  if (num === null) return ['==', ['get', prop], literal]
  return ['any', ['==', ['get', prop], literal], ['==', ['get', prop], num]]
}

const CMP_OPS: Record<string, string> = {
  PropertyIsLessThan: '<',
  PropertyIsGreaterThan: '>',
  PropertyIsLessThanOrEqualTo: '<=',
  PropertyIsGreaterThanOrEqualTo: '>=',
}

/** Переводит элемент фильтра ogc в выражение MapLibre; null — не поддержано. */
function filterToExpr(el: Element, props: Set<string>, unsupported: string[]): MlExpr | null {
  const name = el.localName ?? ''

  if (name === 'Filter') {
    const inner = Array.from(el.children)[0]
    return inner ? filterToExpr(inner, props, unsupported) : null
  }

  if (name === 'And' || name === 'Or') {
    const parts = Array.from(el.children)
      .map((c) => filterToExpr(c, props, unsupported))
      .filter((e) => e !== null)
    if (parts.length === 0) return null
    return [name === 'And' ? 'all' : 'any', ...parts]
  }

  if (name === 'Not') {
    const inner = Array.from(el.children)[0]
    const e = inner ? filterToExpr(inner, props, unsupported) : null
    return e ? ['!', e] : null
  }

  const propEl = childByName(el, 'PropertyName')
  const prop = text(propEl).trim()
  if (prop) props.add(prop)

  if (name === 'PropertyIsEqualTo' || name === 'PropertyIsNotEqualTo') {
    // Литерал берём как есть, без trim: QGIS кладёт в значения переводы строк,
    // и они значимы для точного совпадения с атрибутом.
    const lit = text(childByName(el, 'Literal'))
    if (!prop) return null
    const eq = eqExpr(prop, lit)
    return name === 'PropertyIsEqualTo' ? eq : ['!', eq]
  }

  if (name in CMP_OPS) {
    const lit = literalNumber(text(childByName(el, 'Literal')))
    if (!prop || lit === null) return null
    return ['all', ['has', prop], [CMP_OPS[name], numericGet(prop), lit]]
  }

  if (name === 'PropertyIsBetween') {
    const lo = literalNumber(text(childByName(childByName(el, 'LowerBoundary'), 'Literal')))
    const hi = literalNumber(text(childByName(childByName(el, 'UpperBoundary'), 'Literal')))
    if (!prop || lo === null || hi === null) return null
    return ['all', ['has', prop], ['>=', numericGet(prop), lo], ['<=', numericGet(prop), hi]]
  }

  if (name === 'PropertyIsNull') {
    if (!prop) return null
    return ['any', ['!', ['has', prop]], ['==', ['get', prop], null]]
  }

  unsupported.push(`Фильтр ${name}`)
  return null
}

// ── Разбор правил ──────────────────────────────────────────────────────────

interface ParsedRule {
  title: string
  cond: MlExpr | null // null → ElseFilter/без фильтра (ветка по умолчанию)
  isElse: boolean
  polygon?: { fill?: string; fillOpacity: number; stroke?: string; strokeWidth: number }
  line?: { stroke: string; strokeWidth: number }
  point?: { fill: string; size: number; stroke?: string; strokeWidth: number }
}

function parseRule(rule: Element, props: Set<string>, unsupported: string[]): ParsedRule {
  const title =
    text(childByName(childByName(rule, 'Description'), 'Title')).trim() ||
    text(childByName(rule, 'Name')).trim()

  const isElse = childByName(rule, 'ElseFilter') !== null
  const filterEl = childByName(rule, 'Filter')
  const cond = isElse || !filterEl ? null : filterToExpr(filterEl, props, unsupported)

  const out: ParsedRule = { title, cond, isElse }

  if (childByName(rule, 'MinScaleDenominator') || childByName(rule, 'MaxScaleDenominator')) {
    unsupported.push('Масштабные диапазоны правила (Min/MaxScaleDenominator)')
  }

  const poly = childByName(rule, 'PolygonSymbolizer')
  if (poly) {
    const fill = svgParams(childByName(poly, 'Fill'))
    const stroke = svgParams(childByName(poly, 'Stroke'))
    if (childByName(poly, 'Fill') && childByName(childByName(poly, 'Fill'), 'GraphicFill')) {
      unsupported.push('Штриховка/GraphicFill в PolygonSymbolizer')
    }
    if (stroke['stroke-dasharray']) unsupported.push('Пунктир (stroke-dasharray)')
    out.polygon = {
      fill: fill['fill'],
      fillOpacity: literalNumber(fill['fill-opacity'] ?? '') ?? 1,
      stroke: stroke['stroke'],
      strokeWidth: literalNumber(stroke['stroke-width'] ?? '') ?? 1,
    }
  }

  const lineSym = childByName(rule, 'LineSymbolizer')
  if (lineSym) {
    const stroke = svgParams(childByName(lineSym, 'Stroke'))
    if (stroke['stroke-dasharray']) unsupported.push('Пунктир (stroke-dasharray)')
    out.line = {
      stroke: stroke['stroke'] ?? '#000000',
      strokeWidth: literalNumber(stroke['stroke-width'] ?? '') ?? 1,
    }
  }

  const pointSym = childByName(rule, 'PointSymbolizer')
  if (pointSym) {
    const graphic = childByName(pointSym, 'Graphic')
    const mark = graphic ? childByName(graphic, 'Mark') : null
    if (graphic && childByName(graphic, 'ExternalGraphic')) {
      unsupported.push('ExternalGraphic (внешняя картинка символа)')
    }
    const wkn = text(childByName(mark, 'WellKnownName')).trim() || 'circle'
    if (wkn && wkn !== 'circle') {
      unsupported.push(`Форма символа «${wkn}» отрисована кружком`)
    }
    const fill = svgParams(childByName(mark, 'Fill'))
    const stroke = svgParams(childByName(mark, 'Stroke'))
    // se:Size — диаметр символа в пикселях.
    const size = literalNumber(text(childByName(graphic, 'Size'))) ?? 6
    out.point = {
      fill: fill['fill'] ?? '#1f77b4',
      size,
      stroke: stroke['stroke'],
      strokeWidth: literalNumber(stroke['stroke-width'] ?? '') ?? 0,
    }
  }

  if (childByName(rule, 'TextSymbolizer')) unsupported.push('Подписи (TextSymbolizer)')
  if (childByName(rule, 'RasterSymbolizer')) unsupported.push('RasterSymbolizer (растровый стиль)')

  return out
}

/**
 * Собирает ['case', …] по правилам. pick возвращает значение свойства для
 * правила либо undefined, если правило это свойство не задаёт.
 */
function buildCase(
  rules: ParsedRule[],
  pick: (r: ParsedRule) => string | number | undefined,
  fallback: string | number,
): MlExpr {
  const branches: MlExpr[] = []
  let elseValue: string | number | undefined

  for (const r of rules) {
    const v = pick(r)
    if (v === undefined) continue
    if (r.cond === null) {
      // ElseFilter или правило без фильтра — ветка по умолчанию.
      if (elseValue === undefined) elseValue = v
      continue
    }
    branches.push(r.cond, v)
  }

  const def = elseValue ?? fallback
  if (branches.length === 0) return def
  return ['case', ...branches, def]
}

/** Разбирает SLD; возвращает null, если правил со стилями не найдено. */
export function parseSld(xml: string): SldStyle | null {
  const doc = new DOMParser().parseFromString(xml, 'application/xml')
  if (doc.getElementsByTagName('parsererror').length > 0) {
    throw new Error('SLD не является корректным XML')
  }

  const unsupported: string[] = []
  const props = new Set<string>()

  const ruleEls = descendantsByName(doc, 'Rule')
  if (ruleEls.length === 0) return null

  const fts = descendantsByName(doc, 'FeatureTypeStyle')
  if (fts.length > 1) unsupported.push('Несколько FeatureTypeStyle — применён первый набор правил')
  const userStyles = descendantsByName(doc, 'UserStyle')
  if (userStyles.length > 1) unsupported.push('Несколько UserStyle — применён первый')

  const rules = ruleEls.map((r) => parseRule(r, props, unsupported))

  const name =
    text(childByName(userStyles[0] ?? doc.documentElement, 'Name')).trim() || undefined

  const style: SldStyle = {
    name,
    legend: [],
    propertyNames: Array.from(props),
    unsupported: Array.from(new Set(unsupported)),
    ruleCount: rules.length,
  }

  if (rules.some((r) => r.polygon)) {
    style.fill = {
      color: buildCase(rules, (r) => r.polygon?.fill, 'transparent'),
      opacity: buildCase(rules, (r) => (r.polygon ? r.polygon.fillOpacity : undefined), 1),
    }
    if (rules.some((r) => r.polygon?.stroke)) {
      style.outline = {
        color: buildCase(rules, (r) => r.polygon?.stroke, '#333333'),
        width: buildCase(rules, (r) => (r.polygon?.stroke ? r.polygon.strokeWidth : undefined), 1),
      }
    }
  }

  if (rules.some((r) => r.line)) {
    style.line = {
      color: buildCase(rules, (r) => r.line?.stroke, '#333333'),
      width: buildCase(rules, (r) => r.line?.strokeWidth, 1),
    }
  }

  if (rules.some((r) => r.point)) {
    style.circle = {
      color: buildCase(rules, (r) => r.point?.fill, '#1f77b4'),
      radius: buildCase(rules, (r) => (r.point ? r.point.size / 2 : undefined), 3),
      strokeColor: buildCase(rules, (r) => r.point?.stroke, '#ffffff'),
      strokeWidth: buildCase(rules, (r) => (r.point ? r.point.strokeWidth : undefined), 0),
    }
  }

  style.legend = rules.map((r) => ({
    label: r.title || (r.isElse ? 'Прочие' : '—'),
    color: r.polygon?.fill ?? r.point?.fill ?? r.line?.stroke ?? '#cccccc',
    strokeColor: r.polygon?.stroke ?? r.point?.stroke ?? undefined,
    kind: r.polygon ? 'polygon' : r.point ? 'point' : 'line',
  }))

  return style
}

// ── Растровый стиль (RasterSymbolizer/ColorMap) ─────────────────────────────
// Для растров MapLibre-выражения не нужны: раскраску делает titiler по
// дискретной палитре {значение: цвет}. Поддержан type="values" (классы) — его
// экспортирует GIS для вариантов зонирования ЛП; ramp/intervals переводятся в
// ту же палитру по точным значениям ColorMapEntry.

export interface RasterColorMapEntry {
  quantity: number
  color: string
  opacity: number
  label: string
}

export interface RasterColorMap {
  type: 'values' | 'ramp' | 'intervals'
  entries: RasterColorMapEntry[]
}

/** Разбирает ColorMap первого RasterSymbolizer; null, если его нет. */
export function parseRasterColorMap(xml: string): RasterColorMap | null {
  const doc = new DOMParser().parseFromString(xml, 'application/xml')
  if (doc.getElementsByTagName('parsererror').length > 0) {
    throw new Error('SLD не является корректным XML')
  }
  const cm = descendantsByName(doc, 'ColorMap')[0]
  if (!cm) return null
  const rawType = (cm.getAttribute('type') ?? 'ramp').toLowerCase()
  const type = rawType === 'values' || rawType === 'intervals' ? rawType : 'ramp'
  const entries = childrenByName(cm, 'ColorMapEntry')
    .map((e) => ({
      quantity: Number(e.getAttribute('quantity')),
      color: e.getAttribute('color') ?? '#cccccc',
      opacity: e.hasAttribute('opacity') ? Number(e.getAttribute('opacity')) : 1,
      label: e.getAttribute('label') ?? e.getAttribute('quantity') ?? '',
    }))
    .filter((e) => Number.isFinite(e.quantity))
  if (entries.length === 0) return null
  return { type, entries }
}

/** Палитра для titiler ?colormap=: {целое значение: '#rrggbb[aa]'}. */
export function rasterColorMapToTitiler(cm: RasterColorMap): Record<string, string> {
  const out: Record<string, string> = {}
  for (const e of cm.entries) {
    if (!Number.isInteger(e.quantity) || e.opacity <= 0) continue
    const alpha = e.opacity >= 1 ? '' : Math.round(e.opacity * 255).toString(16).padStart(2, '0')
    out[String(e.quantity)] = `${e.color}${alpha}`
  }
  return out
}
