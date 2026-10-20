'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  createIndicator,
  deleteIndicator,
  fetchDirections,
  fetchIndicators,
  fetchUnits,
  updateIndicator,
  type Indicator,
  type IndicatorCreate,
  type IndicatorUpdate,
  type Unit,
} from '@/lib/api/backend'
import type { Direction } from '@/types/direction'

interface FormState {
  direction_id: string
  unit_id: string
  name: string
  sort_order: string
  is_active: boolean
}

const EMPTY_FORM: FormState = {
  direction_id: '',
  unit_id: '',
  name: '',
  sort_order: '0',
  is_active: true,
}

const inputCls =
  'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500'
const labelCls = 'block text-sm font-medium text-gray-700 mb-1'

export default function IndicatorsTab() {
  const [items, setItems] = useState<Indicator[]>([])
  const [directions, setDirections] = useState<Direction[]>([])
  const [units, setUnits] = useState<Unit[]>([])
  const [isLoading, setIsLoading] = useState(false)

  const [filterDirection, setFilterDirection] = useState('')
  const [search, setSearch] = useState('')

  const [showModal, setShowModal] = useState(false)
  const [editItem, setEditItem] = useState<Indicator | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [modalError, setModalError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)

  const [deleteTarget, setDeleteTarget] = useState<Indicator | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)

  useEffect(() => {
    fetchDirections().then(setDirections).catch(() => {})
    fetchUnits({ is_active: true }).then(setUnits).catch(() => {})
  }, [])

  const load = useCallback(async () => {
    setIsLoading(true)
    const params: { direction_id?: string; search?: string } = {}
    if (filterDirection) params.direction_id = filterDirection
    if (search.trim().length >= 2) params.search = search.trim()
    setItems(await fetchIndicators(params))
    setIsLoading(false)
  }, [filterDirection, search])

  useEffect(() => { load() }, [load])

  function openCreate() {
    setEditItem(null)
    setForm({
      ...EMPTY_FORM,
      direction_id: filterDirection || directions[0]?.id || '',
      unit_id: units[0]?.id || '',
    })
    setModalError(null)
    setShowModal(true)
  }

  function openEdit(it: Indicator) {
    setEditItem(it)
    setForm({
      direction_id: it.direction_id,
      unit_id: it.unit_id,
      name: it.name,
      sort_order: String(it.sort_order),
      is_active: it.is_active,
    })
    setModalError(null)
    setShowModal(true)
  }

  async function handleSave() {
    if (!form.direction_id || !form.unit_id || !form.name.trim()) {
      setModalError('Заполните обязательные поля: Направление, Единица, Наименование')
      return
    }
    const sort = Number(form.sort_order)
    if (Number.isNaN(sort)) {
      setModalError('Поле «Порядок» должно быть числом')
      return
    }
    setIsSaving(true)
    setModalError(null)
    try {
      if (editItem) {
        const body: IndicatorUpdate = {
          direction_id: form.direction_id,
          unit_id: form.unit_id,
          name: form.name.trim(),
          sort_order: sort,
          is_active: form.is_active,
        }
        await updateIndicator(editItem.id, body)
      } else {
        const body: IndicatorCreate = {
          direction_id: form.direction_id,
          unit_id: form.unit_id,
          name: form.name.trim(),
          sort_order: sort,
          is_active: form.is_active,
        }
        await createIndicator(body)
      }
      setShowModal(false)
      load()
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Ошибка при сохранении'
      setModalError(msg)
    } finally {
      setIsSaving(false)
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      await deleteIndicator(deleteTarget.id)
      setDeleteTarget(null)
      load()
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Ошибка при удалении'
      setDeleteError(msg)
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <select
          value={filterDirection}
          onChange={(e) => setFilterDirection(e.target.value)}
          className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        >
          <option value="">Все направления</option>
          {directions.map((d) => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Поиск по наименованию (от 2 символов)..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-72 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        />
        <button
          onClick={openCreate}
          className="ml-auto rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700"
        >
          + Добавить показатель
        </button>
      </div>

      <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              {['Направление', 'Наименование показателя', 'Ед. изм.', 'Пор.', 'Статус', ''].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 6 }).map((__, j) => (
                    <td key={j} className="px-4 py-3"><div className="h-4 rounded bg-gray-200 animate-pulse" /></td>
                  ))}
                </tr>
              ))
            ) : items.length === 0 ? (
              <tr><td colSpan={6} className="py-10 text-center text-sm text-gray-400">Показатели не найдены</td></tr>
            ) : (
              items.map((it) => (
                <tr key={it.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-gray-700">{it.direction_name ?? '—'}</td>
                  <td className="px-4 py-3 text-gray-900">{it.name}</td>
                  <td className="px-4 py-3 text-gray-600">{it.unit_name ?? '—'}</td>
                  <td className="px-4 py-3 text-center text-gray-500">{it.sort_order}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                      it.is_active
                        ? 'bg-green-50 text-green-700 ring-1 ring-green-200'
                        : 'bg-gray-100 text-gray-500 ring-1 ring-gray-200'
                    }`}>
                      {it.is_active ? 'Активен' : 'Неактивен'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button onClick={() => openEdit(it)} className="rounded-md px-2 py-1 text-xs font-medium text-primary-600 hover:bg-primary-50">Изменить</button>
                      <button onClick={() => { setDeleteTarget(it); setDeleteError(null) }} className="rounded-md px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50">Удалить</button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 text-sm text-gray-500">Всего: {items.length}</div>

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-5">
              <h3 className="text-base font-semibold text-gray-900">
                {editItem ? 'Редактировать показатель' : 'Новый показатель'}
              </h3>
              <button onClick={() => setShowModal(false)} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
            </div>
            <div className="px-6 py-5 space-y-4">
              {modalError && (
                <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{modalError}</div>
              )}
              <div>
                <label className={labelCls}>Направление <span className="text-red-500">*</span></label>
                <select
                  value={form.direction_id}
                  onChange={(e) => setForm({ ...form, direction_id: e.target.value })}
                  className={inputCls}
                >
                  <option value="">— выберите —</option>
                  {directions.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className={labelCls}>Наименование показателя <span className="text-red-500">*</span></label>
                <textarea
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  maxLength={300}
                  rows={2}
                  placeholder="Индекс физического объема ВРП"
                  className={inputCls}
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className={labelCls}>Единица измерения <span className="text-red-500">*</span></label>
                  <select
                    value={form.unit_id}
                    onChange={(e) => setForm({ ...form, unit_id: e.target.value })}
                    className={inputCls}
                  >
                    <option value="">— выберите —</option>
                    {units.map((u) => (
                      <option key={u.id} value={u.id}>{u.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className={labelCls}>Порядок отображения</label>
                  <input
                    type="number"
                    value={form.sort_order}
                    onChange={(e) => setForm({ ...form, sort_order: e.target.value })}
                    className={inputCls}
                  />
                </div>
              </div>
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.is_active}
                  onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                  className="h-4 w-4 rounded accent-primary-600"
                />
                <span className="text-sm font-medium text-gray-700">Активен</span>
              </label>
            </div>
            <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
              <button onClick={() => setShowModal(false)} className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">Отмена</button>
              <button onClick={handleSave} disabled={isSaving} className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:opacity-50">
                {isSaving ? 'Сохранение...' : 'Сохранить'}
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="w-full max-w-sm rounded-2xl bg-white shadow-2xl">
            <div className="px-6 py-5">
              <h3 className="text-base font-semibold text-gray-900 mb-2">Удалить показатель?</h3>
              <p className="text-sm text-gray-600">{deleteTarget.name}</p>
              {deleteError && (
                <div className="mt-3 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{deleteError}</div>
              )}
            </div>
            <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
              <button onClick={() => { setDeleteTarget(null); setDeleteError(null) }} className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">Отмена</button>
              <button onClick={handleDelete} disabled={isDeleting} className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50">
                {isDeleting ? 'Удаление...' : 'Удалить'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
