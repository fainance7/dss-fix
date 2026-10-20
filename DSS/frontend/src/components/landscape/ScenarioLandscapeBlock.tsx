'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import {
  apiErrorMessage,
  fetchLandscapeZoning,
  setScenarioLandscapeVariant,
  type LandscapeZoning,
  type ScenarioLandscapeSnapshot,
} from '@/lib/api/landscape'
import { DEFAULT_VARIANT, LANDSCAPE_VARIANTS } from '@/lib/landscape/registry'

/**
 * Вариант ландшафтного планирования в карточке сценария развития.
 *
 * Связь смысловая: сценарий хранит снимок зонирования выбранного варианта (доли
 * функциональных зон по области и районам), он же уходит в контекст заключений
 * ИИ. Численного влияния на KPI нет — классы ЛП не являются категориями земель,
 * расчёт через ЗБ-УР включится после слоя категорий земель от АЛДИС.
 */

function pctRu(v: number, digits = 1): string {
  return v.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function ZoneBar({ zoning }: { zoning: LandscapeZoning }) {
  return (
    <div className="flex h-2.5 w-full overflow-hidden rounded bg-gray-100">
      {zoning.legend.map((e) => {
        const pct = zoning.oblast_pct[String(e.value)] ?? 0
        if (pct <= 0) return null
        return (
          <div
            key={e.value}
            title={`${e.label}: ${pctRu(pct)} %`}
            style={{ width: `${pct}%`, background: e.color ?? '#9ca3af' }}
          />
        )
      })}
    </div>
  )
}

function topZones(zoning: LandscapeZoning, pct: Record<string, number>, n = 2): string {
  const label = new Map(zoning.legend.map((e) => [String(e.value), e.label]))
  return Object.entries(pct)
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([k, v]) => `${label.get(k) ?? k} ${pctRu(v, 0)} %`)
    .join(', ')
}

function VariantModal({
  scenarioId,
  current,
  onClose,
  onSaved,
}: {
  scenarioId: string
  current: string
  onClose: () => void
  onSaved: () => void
}) {
  const [variant, setVariant] = useState(current || DEFAULT_VARIANT)
  const [zoning, setZoning] = useState<LandscapeZoning | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setZoning(null)
    setError(null)
    setLoading(true)
    fetchLandscapeZoning(variant)
      .then((z) => alive && setZoning(z))
      .catch((e) => alive && setError(apiErrorMessage(e, 'Не удалось посчитать доли зон')))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [variant])

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await setScenarioLandscapeVariant(scenarioId, variant)
      onSaved()
    } catch (e) {
      setError(apiErrorMessage(e, 'Не удалось сохранить вариант'))
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl bg-white p-5 shadow-xl">
        <div className="mb-3 flex items-start justify-between">
          <h3 className="font-semibold text-gray-900">Вариант ландшафтного планирования для сценария</h3>
          <button onClick={onClose} disabled={saving} className="text-gray-400 hover:text-gray-600" aria-label="Закрыть">
            ✕
          </button>
        </div>

        <div className="mb-3 grid gap-2 sm:grid-cols-3">
          {LANDSCAPE_VARIANTS.map((v) => (
            <label
              key={v.group}
              className={`cursor-pointer rounded-lg border p-2.5 text-sm ${
                variant === v.group ? 'border-primary-400 bg-primary-50' : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <input type="radio" className="mr-2" checked={variant === v.group} onChange={() => setVariant(v.group)} />
              <span className="font-medium text-gray-900">{v.label}</span>
              <p className="mt-1 text-xs text-gray-500">{v.hint}</p>
            </label>
          ))}
        </div>

        <div className="min-h-[160px] flex-1 overflow-y-auto rounded-lg bg-gray-50 p-3 text-sm">
          {loading && <p className="text-gray-500">Считаем доли функциональных зон по районам… (первый раз до 20 с)</p>}
          {zoning && (
            <>
              <div className="mb-1 text-xs font-medium text-gray-600">Доли зон по области</div>
              <ZoneBar zoning={zoning} />
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-gray-600">
                {zoning.legend.map((e) => (
                  <span key={e.value} className="flex items-center gap-1">
                    <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: e.color ?? '#9ca3af' }} />
                    {e.label} — {pctRu(zoning.oblast_pct[String(e.value)] ?? 0)} %
                  </span>
                ))}
              </div>
              <table className="mt-3 w-full text-xs">
                <thead className="text-left text-gray-500">
                  <tr>
                    <th className="py-1 font-medium">Район</th>
                    <th className="py-1 font-medium">Преобладающие зоны</th>
                  </tr>
                </thead>
                <tbody>
                  {zoning.districts.map((d) => (
                    <tr key={d.kato} className="border-t border-gray-200">
                      <td className="py-1 pr-3 text-gray-900">{d.name}</td>
                      <td className="py-1 text-gray-700">{topZones(zoning, d.pct, 3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>

        <p className="mt-3 text-xs text-gray-500">
          Вариант задаёт функциональное зонирование и уходит в контекст заключений ИИ. На KPI сценария он пока не
          влияет: классы ландшафтного планирования — не категории земель, численный расчёт через ЗБ-УР включится
          после получения слоя категорий земель. После смены варианта заключения ИИ этого сценария нужно
          перегенерировать.
        </p>
        {error && <div className="mt-2 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</div>}

        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} disabled={saving} className="rounded-md border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
            Отмена
          </button>
          <button
            onClick={save}
            disabled={saving || loading || !zoning || variant === current}
            className="rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-40"
          >
            {saving ? 'Сохранение…' : 'Сохранить'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function ScenarioLandscapeBlock({
  scenarioId,
  snapshot,
  canEdit,
  onChanged,
}: {
  scenarioId: string
  snapshot: ScenarioLandscapeSnapshot | null | undefined
  /** Вариант меняют эксперт, аким области и администратор — до утверждения. */
  canEdit: boolean
  onChanged: () => void
}) {
  const [open, setOpen] = useState(false)
  const variant = snapshot?.variant ?? DEFAULT_VARIANT

  return (
    <div className="mb-4 rounded-lg border border-emerald-100 bg-emerald-50/40 p-3">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-gray-500">Ландшафтное планирование</span>
        <Link href={`/dashboard/landscape?variant=${variant}`} className="text-xs text-primary-700 hover:underline">
          На карте →
        </Link>
      </div>
      {snapshot ? (
        <>
          <div className="mb-1.5 text-sm font-semibold text-gray-900">{snapshot.label}</div>
          <ZoneBar zoning={snapshot} />
          <div className="mt-1 text-[11px] text-gray-600">{topZones(snapshot, snapshot.oblast_pct, 3)}</div>
        </>
      ) : (
        <div className="text-sm text-gray-500">Вариант не привязан</div>
      )}
      {canEdit && (
        <button onClick={() => setOpen(true)} className="mt-2 text-xs font-medium text-primary-700 hover:underline">
          {snapshot ? 'Сменить вариант' : 'Привязать вариант'}
        </button>
      )}
      {open && (
        <VariantModal
          scenarioId={scenarioId}
          current={snapshot?.variant ?? ''}
          onClose={() => setOpen(false)}
          onSaved={() => {
            setOpen(false)
            onChanged()
          }}
        />
      )}
    </div>
  )
}
