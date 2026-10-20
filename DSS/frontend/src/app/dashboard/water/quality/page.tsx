'use client'

import { useEffect, useMemo, useState } from 'react'
import TabSummary from '@/components/dashboard/TabSummary'
import ClimateKpiStrip, { type ClimateKpiMetric } from '@/components/dashboard/ClimateKpiStrip'
import { LineChart, WIDE_CHART } from '@/components/dashboard/DirectionDashboard'
import {
  fetchDirections,
  fetchIndicators,
  fetchApprovedValues,
  type Indicator,
  type IndicatorValuesListItem,
} from '@/lib/api/backend'

/**
 * Водный дашборд «Качество воды и водный баланс» (docs/dashboards/water,
 * «ДАШБОРД дополнит водные ресурсы»). Качество р. Есиль (Единая классификация,
 * класс 3–4) и кратность превышения ПДК — областной ряд за 2020–2025; плюс
 * качественная таблица сезонного водного баланса для орошения.
 *
 * Значения WQI/ПДК наполнятся через intake-portal; до этого тренды показывают
 * «Нет данных». Сезонный баланс и меры — статический блок из ТЗ.
 */

// Тренды (областной ряд по годам). Резолвятся по подстроке имени показателя.
const TREND_SERIES: { candidates: string[]; name: string; color: string }[] = [
  { candidates: ['Индекс качества воды', 'WQI'], name: 'Класс качества воды (WQI)', color: '#dc2626' },
  { candidates: ['Превышение ПДК'], name: 'Превышение ПДК, кратность', color: '#ea580c' },
  { candidates: ['Концентрация загрязняющих'], name: 'Концентрация загрязняющих, мг/л', color: '#9333ea' },
]

// Сезонный водный баланс орошения (ТЗ: «Дефицит/профицит водного баланса»).
const SEASON_BALANCE: { period: string; balance: string; tone: 'pos' | 'neg' | 'mid'; note: string }[] = [
  { period: 'Весна (апрель–май)', balance: 'Профицит', tone: 'pos', note: 'Избыточный паводковый сток, наполнение рек и водохранилищ' },
  { period: 'Лето (июнь–август)', balance: 'Дефицит', tone: 'neg', note: 'Высокое испарение и потребление воды на орошение' },
  { period: 'Осень', balance: 'Умеренный дефицит', tone: 'mid', note: 'Снижение стока и уровня грунтовых вод' },
  { period: 'Зима', balance: 'Относительный баланс', tone: 'mid', note: 'Минимальное водопотребление' },
]

const KEY_FACTS: { label: string; value: string }[] = [
  { label: 'Доля сельского хозяйства в водопотреблении', value: '≈ 66 %' },
  { label: 'Основной источник орошения', value: 'р. Есиль, водохранилища, озёра, подземные воды' },
  { label: 'Основная проблема', value: 'неравномерность сезонного стока' },
  { label: 'Цель к 2030 году', value: 'площадь орошаемых земель — до 21 000 га' },
]

const loadValues = (indicatorId: string): Promise<IndicatorValuesListItem[]> =>
  fetchApprovedValues({ indicator_id: indicatorId })

export default function WaterQualityDashboardPage() {
  const [indicators, setIndicators] = useState<Indicator[]>([])
  const [trendData, setTrendData] = useState<{ x: string; y: number; group?: string }[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const resolve = useMemo(() => {
    const byLower = indicators.map((i) => ({ ind: i, lc: i.name.toLowerCase() }))
    return (candidates: string[]): Indicator | null => {
      for (const c of candidates) {
        const needle = c.toLowerCase()
        const hit = byLower.find((x) => x.lc.includes(needle))
        if (hit) return hit.ind
      }
      return null
    }
  }, [indicators])

  // 1) Загрузить показатели направления «Водные ресурсы».
  useEffect(() => {
    let cancel = false
    setLoading(true)
    setError(null)
    fetchDirections()
      .then(async (dirs) => {
        const dir = dirs.find((d) => d.code === 'water')
        if (!dir) {
          if (!cancel) setIndicators([])
          return
        }
        const inds = await fetchIndicators({ direction_id: dir.id, is_active: true })
        if (!cancel) setIndicators(inds)
      })
      .catch((e: unknown) => {
        if (!cancel) setError((e as { message?: string })?.message ?? 'Ошибка загрузки')
      })
      .finally(() => {
        if (!cancel) setLoading(false)
      })
    return () => {
      cancel = true
    }
  }, [])

  // 2) Подгрузить значения для трендов (среднее по году — ряд областной).
  useEffect(() => {
    if (indicators.length === 0) return
    let cancel = false
    const series = TREND_SERIES.map((s) => ({ s, ind: resolve(s.candidates) })).filter(
      (x) => x.ind,
    )
    Promise.all(series.map((x) => loadValues(x.ind!.id)))
      .then((results) => {
        if (cancel) return
        const data: { x: string; y: number; group?: string }[] = []
        results.forEach((vals, i) => {
          const name = series[i].s.name
          const byYear = new Map<string, number[]>()
          for (const v of vals) {
            const yr = v.period_start.slice(0, 4)
            const arr = byYear.get(yr) ?? []
            arr.push(v.value)
            byYear.set(yr, arr)
          }
          for (const [yr, arr] of byYear) {
            data.push({ x: yr, y: arr.reduce((a, b) => a + b, 0) / arr.length, group: name })
          }
        })
        setTrendData(data)
      })
      .catch(() => {
        if (!cancel) setTrendData([])
      })
    return () => {
      cancel = true
    }
  }, [indicators, resolve])

  const kpiMetrics: ClimateKpiMetric[] = useMemo(() => {
    const wqi = resolve(['Индекс качества воды', 'WQI'])?.id ?? null
    const pdk = resolve(['Превышение ПДК'])?.id ?? null
    return [
      { label: 'Класс качества воды р. Есиль (последний)', indicatorId: wqi, agg: 'max', unit: 'класс', decimals: 0 },
      { label: 'Макс. кратность превышения ПДК', indicatorId: pdk, agg: 'max', unit: 'ПДК', decimals: 1 },
      { label: 'Средняя кратность ПДК', indicatorId: pdk, agg: 'avg', unit: 'ПДК', decimals: 1 },
    ]
  }, [resolve])

  const toneClass = (tone: 'pos' | 'neg' | 'mid') =>
    tone === 'pos'
      ? 'bg-blue-50 text-blue-700 ring-blue-100'
      : tone === 'neg'
        ? 'bg-orange-50 text-orange-700 ring-orange-100'
        : 'bg-amber-50 text-amber-700 ring-amber-100'

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-bold text-gray-900">
          Качество воды и водный баланс
        </h1>
        <p className="text-base text-gray-500 mt-1">
          Водные ресурсы · Северо-Казахстанская область · качество р. Есиль (2020–2025) и сезонный водный баланс орошения.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-base text-red-700">
          {error}
        </div>
      )}

      {!loading && <ClimateKpiStrip title="Качество воды р. Есиль" metrics={kpiMetrics} />}

      {!loading && (
        <TabSummary
          tabId="main"
          title="Водные ресурсы — качество воды р. Есиль"
          directionCode="water"
          indicatorIds={TREND_SERIES.map((x) => resolve(x.candidates)?.id)}
        />
      )}

      {/* Тренды качества воды */}
      <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5">
        <h3 className="text-base font-semibold text-gray-900 mb-1">
          Динамика качества воды р. Есиль, 2020–2025
        </h3>
        <p className="text-sm text-gray-500 mb-3">
          Класс качества (Единая классификация) и кратность превышения ПДК по основным загрязнителям
          (магний, сульфаты, нефтепродукты, аммоний, железо, орг. соединения).
        </p>
        <LineChart
          data={trendData}
          unit="класс качества / кратность ПДК / мг/л"
          width={WIDE_CHART.width}
          height={WIDE_CHART.height}
        />
        {trendData.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-3 text-sm text-gray-600">
            {Array.from(new Set(trendData.map((p) => p.group ?? ''))).map((g, gi) => (
              <span key={g} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-2 w-3 rounded-sm"
                  style={{ background: ['#2563eb', '#16a34a', '#dc2626', '#9333ea', '#ea580c', '#0891b2'][gi % 6] }}
                />
                {g}
              </span>
            ))}
          </div>
        )}
      </section>

      {/* Сезонный водный баланс орошения (статический блок из ТЗ) */}
      <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5">
        <h3 className="text-base font-semibold text-gray-900 mb-1">
          Дефицит / профицит водного баланса для орошения
        </h3>
        <p className="text-sm text-gray-500 mb-3">
          СКО относится к бассейну р. Есиль с сезонным дефицитом водных ресурсов: весенний паводковый
          профицит сменяется летним дефицитом из-за испарения и орошения.
        </p>
        <div className="overflow-x-auto">
          <table className="min-w-full text-base">
            <thead>
              <tr className="text-left text-sm text-gray-500 border-b border-gray-200">
                <th className="px-2 py-2">Период</th>
                <th className="px-2 py-2">Баланс</th>
                <th className="px-2 py-2">Характеристика</th>
              </tr>
            </thead>
            <tbody>
              {SEASON_BALANCE.map((r) => (
                <tr key={r.period} className="border-b border-gray-100">
                  <td className="px-2 py-2 font-medium text-gray-700">{r.period}</td>
                  <td className="px-2 py-2">
                    <span className={`inline-block rounded-full px-2.5 py-0.5 text-sm font-medium ring-1 ${toneClass(r.tone)}`}>
                      {r.balance}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-gray-600">{r.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Ключевые факты и меры */}
      <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 p-5">
        <h3 className="text-base font-semibold text-gray-900 mb-3">Основные показатели и меры</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {KEY_FACTS.map((f) => (
            <div key={f.label} className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3">
              <div className="text-sm text-gray-500">{f.label}</div>
              <div className="mt-1 text-base font-semibold text-gray-900">{f.value}</div>
            </div>
          ))}
        </div>
        <div className="mt-4 text-sm text-gray-600 leading-relaxed">
          <span className="font-medium text-gray-700">Меры по снижению дефицита:</span>{' '}
          расширение площадей орошения, внедрение дождевальных машин и водосберегающих технологий,
          реконструкция гидротехнических сооружений, более полное использование подземных вод и
          пресных озёр.
        </div>
      </section>
    </div>
  )
}
