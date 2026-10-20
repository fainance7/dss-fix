'use client'

// ТЕСТОВАЯ страница: проверка отображения датасетов dssgis со стилями SLD.
// Геометрия берётся из dssgis через прокси /api/v1/gis/datasets/{id}.geojson,
// стиль — из статических файлов public/sld/*.sld (примеры от смежной команды,
// оригиналы в data/geo/SLDs). Постоянного хранилища стилей пока нет — это
// проверка того, что транслятор SLD → MapLibre даёт ту же картинку, что QGIS.

import { useEffect, useMemo, useState } from 'react'
import dynamic from 'next/dynamic'
import { gisGetDatasetGeojson, type GeoJsonFeatureCollection } from '@/lib/api/backend'
import { parseSld, type SldStyle } from '@/lib/gis/sld'

const DatasetGeojsonMap = dynamic(() => import('@/components/gis/DatasetGeojsonMap'), {
  ssr: false,
  loading: () => <div className="h-full min-h-[320px] animate-pulse rounded-lg bg-gray-100" />,
})

interface Sample {
  key: string
  label: string
  datasetId: string
  sldUrl: string
}

const SAMPLES: Sample[] = [
  {
    key: 'agro',
    label: 'Агроклиматическое зонирование (полигоны, 5 категорий)',
    datasetId: '9c2fe4dd-5601-43ff-967c-ef3fe84345c9',
    sldUrl: '/sld/agro-zoning.sld',
  },
  {
    key: 'precip',
    label: 'Сумма осадков за вегетационный период (полигоны, 3 категории)',
    datasetId: 'e0048709-63ef-41bf-972f-1a664150cf13',
    sldUrl: '/sld/precip-veg.sld',
  },
  {
    key: 'animals',
    label: 'Животные Красной книги (точки, 8 категорий)',
    datasetId: 'f9d61974-5d6f-4d24-b95f-42877b589333',
    sldUrl: '/sld/red-book-animals.sld',
  },
]

export default function GisSldTestPage() {
  const [sampleKey, setSampleKey] = useState(SAMPLES[0].key)
  const [geojson, setGeojson] = useState<GeoJsonFeatureCollection | null>(null)
  const [sldText, setSldText] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [useSld, setUseSld] = useState(true)

  const sample = SAMPLES.find((s) => s.key === sampleKey)!

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setErr(null)
    setGeojson(null)
    setSldText(null)
    Promise.all([
      gisGetDatasetGeojson(sample.datasetId),
      fetch(sample.sldUrl).then((r) => {
        if (!r.ok) throw new Error(`SLD ${r.status}`)
        return r.text()
      }),
    ])
      .then(([fc, sld]) => {
        if (cancelled) return
        setGeojson(fc)
        setSldText(sld)
      })
      .catch((e) => !cancelled && setErr(String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [sample.datasetId, sample.sldUrl])

  const parsed = useMemo<{ style: SldStyle | null; error: string | null }>(() => {
    if (!sldText) return { style: null, error: null }
    try {
      return { style: parseSld(sldText), error: null }
    } catch (e) {
      return { style: null, error: String(e) }
    }
  }, [sldText])

  const style = useSld ? parsed.style : null

  return (
    <div className="space-y-4 p-4">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Тест: карты GIS со стилями SLD</h1>
        <p className="mt-1 text-sm text-gray-500">
          Геометрия — из dssgis, символика — из SLD-файла (QGIS-экспорт), переведённого в
          выражения MapLibre. Тестовая страница, не часть рабочего интерфейса.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {SAMPLES.map((s) => (
          <button
            key={s.key}
            onClick={() => setSampleKey(s.key)}
            className={`rounded-lg border px-3 py-1.5 text-sm ${
              s.key === sampleKey
                ? 'border-primary-600 bg-primary-50 font-medium text-primary-700'
                : 'border-gray-300 text-gray-600 hover:bg-gray-50'
            }`}
          >
            {s.label}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-2 text-sm text-gray-600">
          <input type="checkbox" checked={useSld} onChange={(e) => setUseSld(e.target.checked)} />
          Применять SLD
        </label>
      </div>

      {err && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      {parsed.error && (
        <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{parsed.error}</div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="h-[560px] rounded-lg border">
          {loading ? (
            <div className="h-full animate-pulse rounded-lg bg-gray-100" />
          ) : (
            <DatasetGeojsonMap
              key={`${sample.key}:${useSld ? 'sld' : 'plain'}`}
              geojson={geojson}
              sld={style}
            />
          )}
        </div>

        <div className="space-y-3">
          <div className="rounded-lg border p-3">
            <div className="mb-2 text-sm font-medium text-gray-700">Легенда из SLD</div>
            {parsed.style ? (
              <ul className="space-y-1.5">
                {parsed.style.legend.map((it, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-gray-700">
                    <span
                      className={`mt-0.5 inline-block h-3.5 w-3.5 shrink-0 border ${
                        it.kind === 'point' ? 'rounded-full' : 'rounded-sm'
                      }`}
                      style={{
                        backgroundColor: it.color,
                        borderColor: it.strokeColor ?? '#94a3b8',
                      }}
                    />
                    <span className="whitespace-pre-line">{it.label}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="text-xs text-gray-400">—</div>
            )}
          </div>

          <div className="rounded-lg border p-3 text-xs text-gray-600">
            <div className="mb-1 font-medium text-gray-700">Разбор</div>
            <div>Правил: {parsed.style?.ruleCount ?? 0}</div>
            <div>Атрибуты фильтров: {parsed.style?.propertyNames.join(', ') || '—'}</div>
            <div>Объектов в слое: {geojson?.features.length ?? 0}</div>
            {parsed.style && parsed.style.unsupported.length > 0 && (
              <div className="mt-2 text-amber-700">
                <div className="font-medium">Не перенесено:</div>
                <ul className="list-inside list-disc">
                  {parsed.style.unsupported.map((u, i) => (
                    <li key={i}>{u}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
