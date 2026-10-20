'use client'

import { useCallback, useEffect, useState } from 'react'
import { fetchEconomicSectors, type EconomicSector } from '@/lib/api/backend'

export default function EconomicSectorsTab() {
  const [items, setItems] = useState<EconomicSector[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [search, setSearch] = useState('')

  const load = useCallback(async () => {
    setIsLoading(true)
    setItems(await fetchEconomicSectors())
    setIsLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const filtered = items.filter((s) => {
    if (!search.trim()) return true
    const q = search.trim().toLowerCase()
    return (
      s.name.toLowerCase().includes(q) ||
      s.short_name.toLowerCase().includes(q) ||
      s.oked_code.toLowerCase().includes(q) ||
      s.indicator_name.toLowerCase().includes(q)
    )
  })

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input
          type="text"
          placeholder="Поиск по сектору, ОКЭД или показателю..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-80 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        />
        <span className="ml-auto text-xs text-gray-400">
          Справочник только для чтения (заполняется миграцией)
        </span>
      </div>

      <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              {['ОКЭД', 'Сектор', 'Короткая подпись', 'Цвет', 'Показатель ЗП', 'Агрегат', 'Статус'].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading ? (
              Array.from({ length: 6 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 7 }).map((__, j) => (
                    <td key={j} className="px-4 py-3"><div className="h-4 rounded bg-gray-200 animate-pulse" /></td>
                  ))}
                </tr>
              ))
            ) : filtered.length === 0 ? (
              <tr><td colSpan={7} className="py-10 text-center text-sm text-gray-400">Записей не найдено</td></tr>
            ) : (
              filtered.map((s) => (
                <tr key={s.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono font-medium text-gray-900">{s.oked_code}</td>
                  <td className="px-4 py-3 text-gray-900">{s.name}</td>
                  <td className="px-4 py-3 text-gray-600">{s.short_name}</td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center gap-2">
                      <span
                        className="inline-block h-4 w-4 rounded ring-1 ring-gray-300"
                        style={{ backgroundColor: s.color }}
                      />
                      <span className="font-mono text-xs text-gray-500">{s.color}</span>
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-500">{s.indicator_name}</td>
                  <td className="px-4 py-3">
                    {s.is_aggregate ? (
                      <span className="inline-flex items-center rounded-md bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 ring-1 ring-amber-200">
                        агрегат
                      </span>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                      s.is_active
                        ? 'bg-green-50 text-green-700 ring-1 ring-green-200'
                        : 'bg-gray-100 text-gray-500 ring-1 ring-gray-200'
                    }`}>
                      {s.is_active ? 'Активен' : 'Неактивен'}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 text-sm text-gray-500">
        Всего: {filtered.length} {filtered.length !== items.length ? `(из ${items.length})` : ''}
      </div>
    </>
  )
}
