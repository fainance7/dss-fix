'use client'

import { useState, useEffect, useMemo } from 'react'
import { fetchDataLayers, fetchDirections } from '@/lib/api/backend'
import type { DataLayer } from '@/lib/api/backend'
import type { Direction } from '@/types/direction'

const DATA_TYPE_LABELS: Record<string, string> = {
  numeric: 'Числовой',
  text: 'Текстовый',
  boolean: 'Булевый',
  geospatial: 'Геопространственный',
  timeseries: 'Временной ряд',
}

const DATA_TYPE_COLORS: Record<string, string> = {
  numeric: 'bg-blue-100 text-blue-700',
  text: 'bg-gray-100 text-gray-600',
  boolean: 'bg-purple-100 text-purple-700',
  geospatial: 'bg-green-100 text-green-700',
  timeseries: 'bg-yellow-100 text-yellow-700',
}

function SkeletonRow() {
  return (
    <tr className="animate-pulse">
      <td className="px-5 py-3.5">
        <div className="h-4 w-48 rounded bg-gray-200" />
        <div className="mt-1.5 h-3 w-64 rounded bg-gray-100" />
      </td>
      <td className="px-5 py-3.5">
        <div className="h-5 w-24 rounded-full bg-gray-200" />
      </td>
      <td className="px-5 py-3.5">
        <div className="h-4 w-24 rounded bg-gray-200" />
      </td>
      <td className="px-5 py-3.5">
        <div className="h-5 w-20 rounded-full bg-gray-200" />
      </td>
      <td className="px-5 py-3.5">
        <div className="h-4 w-12 rounded bg-gray-200" />
      </td>
      <td className="px-5 py-3.5">
        <div className="h-7 w-20 rounded-lg bg-gray-200" />
      </td>
    </tr>
  )
}

export default function DataCatalogPage() {
  const [layers, setLayers] = useState<DataLayer[]>([])
  const [directions, setDirections] = useState<Direction[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('Все')
  const [directionFilter, setDirectionFilter] = useState('Все')
  const [typeFilter, setTypeFilter] = useState('Все')

  useEffect(() => {
    Promise.all([
      fetchDataLayers().catch(() => [] as DataLayer[]),
      fetchDirections().catch(() => [] as Direction[]),
    ]).then(([layersData, dirsData]) => {
      setLayers(layersData)
      setDirections(dirsData)
    }).finally(() => setLoading(false))
  }, [])

  const categories = useMemo(() => {
    const unique = Array.from(new Set(layers.map((l) => l.category_name).filter(Boolean)))
    return ['Все', ...unique]
  }, [layers])

  const dataTypes = useMemo(() => {
    const unique = Array.from(new Set(layers.map((l) => l.data_type).filter(Boolean)))
    return ['Все', ...unique]
  }, [layers])

  const filtered = useMemo(() => {
    return layers.filter((l) => {
      if (categoryFilter !== 'Все' && l.category_name !== categoryFilter) return false
      if (directionFilter !== 'Все' && l.direction_name !== directionFilter) return false
      if (typeFilter !== 'Все' && l.data_type !== typeFilter) return false
      if (
        search &&
        !l.name.toLowerCase().includes(search.toLowerCase()) &&
        !(l.description ?? '').toLowerCase().includes(search.toLowerCase())
      )
        return false
      return true
    })
  }, [layers, search, categoryFilter, directionFilter, typeFilter])

  return (
    <>
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-gray-900">Каталог данных</h1>
        <p className="mt-0.5 text-sm text-gray-500">
          Слои пространственных данных Северо-Казахстанской области
        </p>
      </div>

      {/* Stats row */}
      {!loading && (
        <div className="mb-6 flex flex-wrap gap-3">
          <div className="rounded-xl bg-white px-4 py-3 shadow-sm ring-1 ring-gray-100 text-center min-w-[100px]">
            <div className="text-xl font-bold text-gray-900">{layers.length}</div>
            <div className="text-xs text-gray-500">Всего слоёв</div>
          </div>
          <div className="rounded-xl bg-white px-4 py-3 shadow-sm ring-1 ring-gray-100 text-center min-w-[100px]">
            <div className="text-xl font-bold text-primary-600">{categories.length - 1}</div>
            <div className="text-xs text-gray-500">Категорий</div>
          </div>
          <div className="rounded-xl bg-white px-4 py-3 shadow-sm ring-1 ring-gray-100 text-center min-w-[100px]">
            <div className="text-xl font-bold text-green-600">{filtered.length}</div>
            <div className="text-xs text-gray-500">Показано</div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="mb-4 flex flex-wrap gap-3">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Поиск по названию или описанию..."
          className="flex-1 min-w-[220px] rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        />
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        >
          {categories.map((c) => (
            <option key={c} value={c}>
              {c === 'Все' ? 'Все категории' : c}
            </option>
          ))}
        </select>
        <select
          value={directionFilter}
          onChange={(e) => setDirectionFilter(e.target.value)}
          className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        >
          <option value="Все">Все направления</option>
          {directions.map((d) => (
            <option key={d.id} value={d.name}>{d.name}</option>
          ))}
        </select>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        >
          {dataTypes.map((t) => (
            <option key={t} value={t}>
              {t === 'Все' ? 'Все типы' : (DATA_TYPE_LABELS[t] ?? t)}
            </option>
          ))}
        </select>
        {(search || categoryFilter !== 'Все' || directionFilter !== 'Все' || typeFilter !== 'Все') && (
          <button
            onClick={() => {
              setSearch('')
              setCategoryFilter('Все')
              setDirectionFilter('Все')
              setTypeFilter('Все')
            }}
            className="rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50"
          >
            Сбросить
          </button>
        )}
      </div>

      {/* Table */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500">Название</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500">Направление</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500">Категория</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500">Тип</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500">Ед. изм.</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {loading ? (
                Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} />)
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-16 text-center text-gray-400">
                    {layers.length === 0
                      ? 'Слои данных не найдены'
                      : 'Ничего не найдено по заданным фильтрам'}
                  </td>
                </tr>
              ) : (
                filtered.map((layer, idx) => {
                  const typeLabel = DATA_TYPE_LABELS[layer.data_type] ?? layer.data_type
                  const typeColor = DATA_TYPE_COLORS[layer.data_type] ?? 'bg-gray-100 text-gray-600'
                  return (
                    <tr
                      key={layer.id}
                      className={idx % 2 === 0 ? 'hover:bg-gray-50/50' : 'bg-gray-50/30 hover:bg-gray-50'}
                    >
                      <td className="px-5 py-3.5">
                        <div className="font-medium text-gray-900 max-w-xs truncate">
                          {layer.name}
                        </div>
                        {layer.description && (
                          <div className="mt-0.5 text-xs text-gray-400 max-w-xs truncate">
                            {layer.description}
                          </div>
                        )}
                      </td>
                      <td className="px-5 py-3.5">
                        <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700">
                          {layer.direction_name ?? '—'}
                        </span>
                      </td>
                      <td className="px-5 py-3.5 text-gray-600">
                        {layer.category_name ?? '—'}
                      </td>
                      <td className="px-5 py-3.5">
                        <span
                          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${typeColor}`}
                        >
                          {typeLabel || '—'}
                        </span>
                      </td>
                      <td className="px-5 py-3.5 text-gray-600 font-mono text-xs">
                        {layer.unit || '—'}
                      </td>
                      <td className="px-5 py-3.5">
                        <a
                          href={`/dashboard/map?layer=${layer.id}`}
                          className="inline-flex items-center gap-1 rounded-lg border border-primary-200 bg-primary-50 px-3 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-100 transition-colors"
                        >
                          На карте
                        </a>
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
