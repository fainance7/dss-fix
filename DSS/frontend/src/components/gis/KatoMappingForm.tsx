'use client'

import { useState } from 'react'
import {
  gisCreateMapping,
  type GisAttributeColumn,
  type GisYearColumn,
} from '@/lib/api/backend'

function errMessage(e: unknown, fallback: string): string {
  const err = e as { response?: { data?: { detail?: string } } }
  return err.response?.data?.detail ?? fallback
}

export interface KatoMappingFormProps {
  datasetId: string
  attributeColumns: GisAttributeColumn[]
  /** Вызывается после успешного сохранения маппинга. */
  onSaved: () => void
}

/**
 * Таблица KATO-маппинга для vector_with_kato: выбор колонки KATO (radio) и
 * колонок-годов со значением показателя (checkbox + год YYYY → YYYY-01-01).
 * Используется и в форме загрузки, и в каталоге (пост-фактум маппинг).
 */
export default function KatoMappingForm({
  datasetId,
  attributeColumns,
  onSaved,
}: KatoMappingFormProps) {
  const [katoColumn, setKatoColumn] = useState('')
  const [yearCols, setYearCols] = useState<Record<string, string>>({}) // column → year (YYYY)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggleYearCol = (col: string) => {
    setYearCols((prev) => {
      const next = { ...prev }
      if (col in next) delete next[col]
      else next[col] = ''
      return next
    })
  }

  const handleSave = async () => {
    if (!katoColumn) return
    const year_columns: GisYearColumn[] = Object.entries(yearCols)
      .filter(([, year]) => /^\d{4}$/.test(year))
      .map(([column, year]) => ({ column, start_date: `${year}-01-01` }))
    if (year_columns.length === 0) {
      setError('Укажите хотя бы одну колонку-год (4 цифры года).')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await gisCreateMapping(datasetId, { kato_column: katoColumn, year_columns })
      onSaved()
    } catch (e) {
      setError(errMessage(e, 'Не удалось сохранить KATO-маппинг'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-gray-600">
        Укажите колонку KATO и колонки-годы со значениями показателя (год — 4 цифры).
      </p>
      {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      {attributeColumns.length === 0 ? (
        <p className="text-sm text-gray-500">В карточке нет списка колонок.</p>
      ) : (
        <div className="overflow-auto rounded-lg border">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left">KATO</th>
                <th className="px-3 py-2 text-left">Год-значение</th>
                <th className="px-3 py-2 text-left">Колонка</th>
                <th className="px-3 py-2 text-left">Примеры</th>
                <th className="px-3 py-2 text-left">Год</th>
              </tr>
            </thead>
            <tbody>
              {attributeColumns.map((c) => (
                <tr key={c.name} className="border-t">
                  <td className="px-3 py-2">
                    <input
                      type="radio"
                      name="kato"
                      checked={katoColumn === c.name}
                      onChange={() => setKatoColumn(c.name)}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={c.name in yearCols}
                      onChange={() => toggleYearCol(c.name)}
                    />
                  </td>
                  <td className="px-3 py-2 font-medium">{c.name}</td>
                  <td className="px-3 py-2 text-xs text-gray-500">
                    {(c.samples ?? []).slice(0, 4).join(', ')}
                  </td>
                  <td className="px-3 py-2">
                    {c.name in yearCols && (
                      <input
                        type="text"
                        inputMode="numeric"
                        maxLength={4}
                        value={yearCols[c.name]}
                        onChange={(e) =>
                          setYearCols((prev) => ({ ...prev, [c.name]: e.target.value }))
                        }
                        placeholder="2023"
                        className="w-20 rounded border px-2 py-1 text-sm"
                      />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <button
        onClick={handleSave}
        disabled={!katoColumn || saving}
        className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white disabled:bg-gray-300"
      >
        {saving ? 'Сохранение…' : 'Сохранить маппинг'}
      </button>
    </div>
  )
}
