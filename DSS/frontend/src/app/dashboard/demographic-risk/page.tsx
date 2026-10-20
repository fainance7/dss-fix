'use client'

import { useEffect, useState } from 'react'
import IndicatorView from '@/components/dashboard/IndicatorView'
import { fetchDirections, fetchIndicators, type Indicator } from '@/lib/api/backend'

const INDICATOR_NAME = 'Демографические риски рынка труда'

export default function DemographicRiskDashboardPage() {
  const [indicator, setIndicator] = useState<Indicator | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancel = false
    setLoading(true)
    setError(null)
    fetchDirections()
      .then(async (dirs) => {
        const dir = dirs.find((d) => d.code === 'social_demog')
        if (!dir) {
          if (!cancel) setIndicator(null)
          return
        }
        const inds = await fetchIndicators({ direction_id: dir.id, is_active: true })
        if (!cancel) setIndicator(inds.find((i) => i.name === INDICATOR_NAME) ?? null)
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

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-bold text-gray-900">
          Демографические риски рынка труда
        </h1>
        <p className="text-base text-gray-500 mt-1">
          Сводный индекс демографического риска рынка труда Северо-Казахстанской
          области. Рассчитывается аналитическим модулем — карта и графики
          заполнятся, когда модель выдаст значения.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-base text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="p-6 text-base text-gray-500">Загрузка показателей…</div>
      ) : !indicator ? (
        <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center text-base text-gray-500">
          Показатель «{INDICATOR_NAME}» не найден в справочнике.
        </div>
      ) : (
        <IndicatorView
          indicatorId={indicator.id}
          indicatorName={indicator.name}
          unit={indicator.unit_name ?? 'индекс'}
          color="#dc2626"
        />
      )}
    </div>
  )
}
