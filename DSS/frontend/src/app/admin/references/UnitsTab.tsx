'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  createUnit,
  deleteUnit,
  fetchUnits,
  updateUnit,
  type Unit,
  type UnitCreate,
  type UnitUpdate,
} from '@/lib/api/backend'

interface FormState {
  name: string
  description: string
  is_active: boolean
}

const EMPTY_FORM: FormState = { name: '', description: '', is_active: true }

const inputCls =
  'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500'
const labelCls = 'block text-sm font-medium text-gray-700 mb-1'

export default function UnitsTab() {
  const [items, setItems] = useState<Unit[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [search, setSearch] = useState('')

  const [showModal, setShowModal] = useState(false)
  const [editItem, setEditItem] = useState<Unit | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [modalError, setModalError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)

  const [deleteTarget, setDeleteTarget] = useState<Unit | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)

  const load = useCallback(async () => {
    setIsLoading(true)
    setItems(await fetchUnits())
    setIsLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const filtered = items.filter((u) => {
    if (!search.trim()) return true
    const q = search.trim().toLowerCase()
    return u.name.toLowerCase().includes(q) || (u.description ?? '').toLowerCase().includes(q)
  })

  function openCreate() {
    setEditItem(null)
    setForm(EMPTY_FORM)
    setModalError(null)
    setShowModal(true)
  }

  function openEdit(u: Unit) {
    setEditItem(u)
    setForm({
      name: u.name,
      description: u.description ?? '',
      is_active: u.is_active,
    })
    setModalError(null)
    setShowModal(true)
  }

  async function handleSave() {
    if (!form.name.trim()) {
      setModalError('Заполните поле «Наименование»')
      return
    }
    setIsSaving(true)
    setModalError(null)
    try {
      if (editItem) {
        const body: UnitUpdate = {
          name: form.name.trim(),
          description: form.description.trim() || null,
          is_active: form.is_active,
        }
        await updateUnit(editItem.id, body)
      } else {
        const body: UnitCreate = {
          name: form.name.trim(),
          description: form.description.trim() || null,
          is_active: form.is_active,
        }
        await createUnit(body)
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
      await deleteUnit(deleteTarget.id)
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
        <input
          type="text"
          placeholder="Поиск по наименованию или описанию..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-72 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        />
        <button
          onClick={openCreate}
          className="ml-auto rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700"
        >
          + Добавить единицу
        </button>
      </div>

      <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              {['Наименование', 'Описание', 'Статус', ''].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 4 }).map((__, j) => (
                    <td key={j} className="px-4 py-3"><div className="h-4 rounded bg-gray-200 animate-pulse" /></td>
                  ))}
                </tr>
              ))
            ) : filtered.length === 0 ? (
              <tr><td colSpan={4} className="py-10 text-center text-sm text-gray-400">Записей не найдено</td></tr>
            ) : (
              filtered.map((u) => (
                <tr key={u.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-medium text-gray-900">{u.name}</td>
                  <td className="px-4 py-3 text-gray-600">{u.description ?? '—'}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                      u.is_active
                        ? 'bg-green-50 text-green-700 ring-1 ring-green-200'
                        : 'bg-gray-100 text-gray-500 ring-1 ring-gray-200'
                    }`}>
                      {u.is_active ? 'Активна' : 'Неактивна'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button onClick={() => openEdit(u)} className="rounded-md px-2 py-1 text-xs font-medium text-primary-600 hover:bg-primary-50">Изменить</button>
                      <button onClick={() => { setDeleteTarget(u); setDeleteError(null) }} className="rounded-md px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50">Удалить</button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 text-sm text-gray-500">Всего: {filtered.length} {filtered.length !== items.length ? `(из ${items.length})` : ''}</div>

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-5">
              <h3 className="text-base font-semibold text-gray-900">
                {editItem ? 'Редактировать единицу' : 'Новая единица измерения'}
              </h3>
              <button onClick={() => setShowModal(false)} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
            </div>
            <div className="px-6 py-5 space-y-4">
              {modalError && (
                <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{modalError}</div>
              )}
              <div>
                <label className={labelCls}>Наименование <span className="text-red-500">*</span></label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  maxLength={100}
                  placeholder="млрд.тенге"
                  className={inputCls}
                />
              </div>
              <div>
                <label className={labelCls}>Описание</label>
                <input
                  type="text"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  maxLength={200}
                  placeholder="Миллиард тенге"
                  className={inputCls}
                />
              </div>
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.is_active}
                  onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                  className="h-4 w-4 rounded accent-primary-600"
                />
                <span className="text-sm font-medium text-gray-700">Активна</span>
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
              <h3 className="text-base font-semibold text-gray-900 mb-2">Удалить единицу?</h3>
              <p className="text-sm text-gray-600"><span className="font-medium text-gray-900">{deleteTarget.name}</span></p>
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
