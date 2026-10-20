'use client'

/**
 * Справочные многолетние таблицы климатического раздела (данные заказчика из
 * «Предложений по корректировке дашборда», период 2003–2024 гг.): даты устойчивого
 * перехода температуры через 5/10/15 °C, суммы активных температур выше 5/10/15 °C,
 * обеспеченность суммы активных температур выше 10 °C. Отдаёт /climate/reference.
 */

import { useEffect, useState, type ReactNode } from 'react'
import { fetchClimateReference, type ClimateReference } from '@/lib/api/climate'
import { formatNumber } from '../chartKit'

let cache: Promise<ClimateReference> | null = null

function useReference(): { data: ClimateReference | null; error: boolean } {
  const [data, setData] = useState<ClimateReference | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    let cancel = false
    cache ??= fetchClimateReference()
    cache
      .then((d) => !cancel && setData(d))
      .catch(() => {
        cache = null
        if (!cancel) setError(true)
      })
    return () => {
      cancel = true
    }
  }, [])
  return { data, error }
}

/** «Кызылжарский район» + станции → «Кызылжарский район (Налобино, Петропавловск)». */
function regionCell(region: string, stations: string[]) {
  return (
    <>
      <span className="text-gray-800">{region}</span>
      <span className="block text-sm text-gray-400">{stations.join(', ')}</span>
    </>
  )
}

function Card({
  title,
  description,
  source,
  children,
}: {
  title: string
  description?: string[]
  source?: string
  children: ReactNode
}) {
  return (
    <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
      <h3 className="mb-3 text-base font-semibold text-gray-900">{title}</h3>
      <div className="overflow-x-auto">{children}</div>
      {description && description.length > 0 && (
        <div className="mt-4 space-y-2 text-base text-gray-600">
          {description.map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </div>
      )}
      {source && <p className="mt-3 text-sm text-gray-400">Источник: {source}.</p>}
    </section>
  )
}

function State({ error, title }: { error: boolean; title: string }) {
  return (
    <Card title={title}>
      <div className="py-6 text-center text-base text-gray-400">
        {error ? 'Не удалось загрузить справочную таблицу' : 'Загрузка…'}
      </div>
    </Card>
  )
}

const th = 'px-3 py-2 text-center font-medium text-gray-600 border border-gray-200 bg-gray-50'
const td = 'px-3 py-1.5 text-center border border-gray-200 tabular-nums'
const tdL = 'px-3 py-1.5 text-left border border-gray-200'

/** Таблица 1: средние многолетние даты перехода через 5, 10, 15 °C и продолжительность периодов. */
export function TransitionsTable() {
  const { data, error } = useReference()
  if (!data) return <State error={error} title="Средние многолетние даты перехода температуры воздуха через 5, 10 и 15 °C" />
  const t = data.transitions
  return (
    <Card title={t.title} description={t.description} source={data.source}>
      <table className="min-w-full border-collapse text-base">
        <thead>
          <tr>
            <th rowSpan={2} className={`${th} text-left`}>Район (метеостанции)</th>
            {[5, 10, 15].map((p) => (
              <th key={p} colSpan={3} className={th}>
                Переход через {p} °C
              </th>
            ))}
          </tr>
          <tr>
            {[5, 10, 15].map((p) => (
              <FragmentCells key={p} p={p} />
            ))}
          </tr>
        </thead>
        <tbody>
          {t.rows.map((r) => (
            <tr key={r.region}>
              <td className={tdL}>{regionCell(r.region, r.stations)}</td>
              <td className={td}>{r.d5_spring}</td>
              <td className={td}>{r.d5_autumn}</td>
              <td className={td}>{r.n5}</td>
              <td className={td}>{r.d10_spring}</td>
              <td className={td}>{r.d10_autumn}</td>
              <td className={td}>{r.n10}</td>
              <td className={td}>{r.d15_spring}</td>
              <td className={td}>{r.d15_autumn}</td>
              <td className={td}>{r.n15}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-sm text-gray-500">
        D — дата устойчивого перехода (день.месяц) весной и осенью; N — продолжительность периода с
        температурой выше указанного предела, сутки.
      </p>
    </Card>
  )
}

function FragmentCells({ p }: { p: number }) {
  return (
    <>
      <th className={th}>D{p}, весна</th>
      <th className={th}>D{p}, осень</th>
      <th className={th}>N{p}, сутки</th>
    </>
  )
}

/** Таблица 2: средние многолетние суммы активных температур выше 5, 10 и 15 °C. */
export function ActiveSumsTable() {
  const { data, error } = useReference()
  if (!data) return <State error={error} title="Средние многолетние суммы активных температур воздуха" />
  const t = data.active_sums
  return (
    <Card title={t.title} description={t.description} source={data.source}>
      <table className="min-w-full border-collapse text-base">
        <thead>
          <tr>
            <th className={`${th} text-left`}>Район (метеостанции)</th>
            <th className={th}>ΣT выше 5 °C, °C·сут</th>
            <th className={th}>ΣT выше 10 °C, °C·сут</th>
            <th className={th}>ΣT выше 15 °C, °C·сут</th>
          </tr>
        </thead>
        <tbody>
          {t.rows.map((r) => (
            <tr key={r.region}>
              <td className={tdL}>{regionCell(r.region, r.stations)}</td>
              <td className={td}>{formatNumber(r.t5, 0)}</td>
              <td className={td}>{formatNumber(r.t10, 0)}</td>
              <td className={td}>{formatNumber(r.t15, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}

/** Таблица 13: обеспеченность (P, %) суммы активных температур выше 10 °C. */
export function ProbabilityTable() {
  const { data, error } = useReference()
  if (!data) return <State error={error} title="Обеспеченность суммы активных температур воздуха выше 10 °C" />
  const t = data.probability
  return (
    <Card title={t.title} description={t.description} source={data.source}>
      <table className="min-w-full border-collapse text-base">
        <thead>
          <tr>
            <th rowSpan={2} className={`${th} text-left`}>Район (метеостанции)</th>
            <th colSpan={t.levels.length} className={th}>
              Обеспеченность P, %
            </th>
          </tr>
          <tr>
            {t.levels.map((l) => (
              <th key={l} className={th}>
                {l}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {t.rows.map((r) => (
            <tr key={r.region}>
              <td className={tdL}>{regionCell(r.region, r.stations)}</td>
              {r.values.map((v, i) => (
                <td key={i} className={`${td} ${t.levels[i] === 90 ? 'bg-amber-50 font-medium' : ''}`}>
                  {formatNumber(v, 0)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-sm text-gray-500">
        Значение при обеспеченности P % — сумма активных температур, которая достигается или
        превышается в P % лет. Выделен уровень 90 % (9 лет из 10).
      </p>
    </Card>
  )
}
