// Многокритериальное сравнение вариантов ЛП — прозрачная взвешенная сумма.
//
// 1. Нормирование по критерию между вариантами: лучший = 1, худший = 0, линейно.
//    Если разброс меньше SIGNIFICANCE от наибольшего значения, критерий считается
//    неразличающим (все варианты = 1) — иначе ничтожная разница получала бы полный вес.
// 2. Итог варианта = Σ wᵢ·sᵢ / Σ wᵢ, шкала 0–100.
// 3. Доминирование — по сырым значениям: вариант A доминирует B, если не хуже по всем
//    критериям и лучше хотя бы по одному. Недоминируемые варианты выбираются только весами.
// 4. Чувствительность: перебор весов трёх блоков по симплексу с шагом 5 % (внутри блока —
//    текущие относительные веса критериев) — доля весов, при которых побеждает каждый вариант.

import type { CompareBlock, CompareCriterion, CompareValues, CompareVariant } from '@/lib/api/landscape'

export const VARIANT_KEYS: CompareVariant[] = ['V1', 'V2', 'V3']
export const BLOCK_KEYS: CompareBlock[] = ['nature', 'economy', 'feasibility']
export const SIGNIFICANCE = 0.05

export type Weights = Record<string, number>

export interface NormalizedCriterion {
  id: string
  scores: Record<CompareVariant, number>
  best: CompareVariant[]
  worst: CompareVariant[]
  /** Разброс меньше порога — критерий не различает варианты. */
  insignificant: boolean
}

export function normalize(c: CompareCriterion, values: CompareValues): NormalizedCriterion {
  const xs = VARIANT_KEYS.map((v) => values[v][c.id] ?? 0)
  const lo = Math.min(...xs)
  const hi = Math.max(...xs)
  const ref = Math.max(Math.abs(lo), Math.abs(hi))
  const insignificant = hi - lo <= SIGNIFICANCE * ref
  const scores = {} as Record<CompareVariant, number>
  for (const v of VARIANT_KEYS) {
    const x = values[v][c.id] ?? 0
    scores[v] = insignificant ? 1 : c.better === 'higher' ? (x - lo) / (hi - lo) : (hi - x) / (hi - lo)
  }
  const top = Math.max(...VARIANT_KEYS.map((v) => scores[v]))
  const bottom = Math.min(...VARIANT_KEYS.map((v) => scores[v]))
  return {
    id: c.id,
    scores,
    insignificant,
    best: insignificant ? [] : VARIANT_KEYS.filter((v) => scores[v] === top),
    worst: insignificant ? [] : VARIANT_KEYS.filter((v) => scores[v] === bottom),
  }
}

export function weightedScores(
  norm: NormalizedCriterion[],
  weights: Weights,
): Record<CompareVariant, number> {
  const total = norm.reduce((a, n) => a + (weights[n.id] ?? 0), 0)
  const out = {} as Record<CompareVariant, number>
  for (const v of VARIANT_KEYS) {
    out[v] = total > 0 ? (100 * norm.reduce((a, n) => a + (weights[n.id] ?? 0) * n.scores[v], 0)) / total : 0
  }
  return out
}

/** Пары «A доминирует B» по сырым значениям (учитываются критерии с ненулевым весом). */
export function dominance(
  criteria: CompareCriterion[],
  values: CompareValues,
  weights: Weights,
): Array<[CompareVariant, CompareVariant]> {
  const used = criteria.filter((c) => (weights[c.id] ?? 0) > 0)
  const better = (c: CompareCriterion, a: number, b: number) => (c.better === 'higher' ? a > b : a < b)
  const out: Array<[CompareVariant, CompareVariant]> = []
  for (const a of VARIANT_KEYS)
    for (const b of VARIANT_KEYS) {
      if (a === b || used.length === 0) continue
      const notWorse = used.every((c) => !better(c, values[b][c.id], values[a][c.id]))
      const strictly = used.some((c) => better(c, values[a][c.id], values[b][c.id]))
      if (notWorse && strictly) out.push([a, b])
    }
  return out
}

export interface SensitivityPoint {
  w: Record<CompareBlock, number>
  winner: CompareVariant | null
}

/** Перебор весов блоков (шаг 5 %) при текущих относительных весах критериев внутри блоков. */
export function sensitivity(
  criteria: CompareCriterion[],
  norm: NormalizedCriterion[],
  weights: Weights,
): { points: SensitivityPoint[]; share: Record<CompareVariant, number> } {
  const byBlock = Object.fromEntries(
    BLOCK_KEYS.map((b) => {
      const cs = criteria.filter((c) => c.block === b)
      const sum = cs.reduce((a, c) => a + (weights[c.id] ?? 0), 0)
      return [b, { cs, sum }]
    }),
  ) as Record<CompareBlock, { cs: CompareCriterion[]; sum: number }>
  const points: SensitivityPoint[] = []
  const wins: Record<CompareVariant, number> = { V1: 0, V2: 0, V3: 0 }
  const STEPS = 20
  for (let i = 0; i <= STEPS; i++)
    for (let j = 0; j <= STEPS - i; j++) {
      const w = { nature: i / STEPS, economy: j / STEPS, feasibility: (STEPS - i - j) / STEPS }
      const cw: Weights = {}
      for (const b of BLOCK_KEYS) {
        const { cs, sum } = byBlock[b]
        for (const c of cs) cw[c.id] = sum > 0 ? (w[b] * (weights[c.id] ?? 0)) / sum : 0
      }
      const s = weightedScores(norm, cw)
      const top = Math.max(...VARIANT_KEYS.map((v) => s[v]))
      const leaders = VARIANT_KEYS.filter((v) => Math.abs(s[v] - top) < 1e-9)
      const winner = leaders.length === 1 ? leaders[0] : null
      if (winner) wins[winner] += 1
      points.push({ w, winner })
    }
  const n = points.length
  return {
    points,
    share: { V1: wins.V1 / n, V2: wins.V2 / n, V3: wins.V3 / n },
  }
}

/** Веса блоков из весов критериев (доли суммы). */
export function blockShares(criteria: CompareCriterion[], weights: Weights): Record<CompareBlock, number> {
  const total = criteria.reduce((a, c) => a + (weights[c.id] ?? 0), 0)
  const out = { nature: 0, economy: 0, feasibility: 0 } as Record<CompareBlock, number>
  for (const c of criteria) out[c.block] += total > 0 ? (weights[c.id] ?? 0) / total : 0
  return out
}
