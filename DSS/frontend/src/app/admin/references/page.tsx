'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthStore } from '@/store/authStore'
import {
  fetchKatoList,
  createKato,
  updateKato,
  deleteKato,
  fetchRegions,
  importKatoCsv,
  getKatoImportStatus,
  type KatoItem,
  type KatoCreate,
  type KatoUpdate,
  type KatoImportStatus,
  type Region,
} from '@/lib/api/backend'
import IndicatorsTab from './IndicatorsTab'
import UnitsTab from './UnitsTab'
import EconomicSectorsTab from './EconomicSectorsTab'

type TabKey = 'kato' | 'indicators' | 'units' | 'sectors'

const PAGE_SIZE = 20

const TYPE_OPTIONS = [
  { value: '', label: '— не указан —' },
  { value: 'oblys', label: 'oblys — Область' },
  { value: 'rayon', label: 'rayon — Район' },
  { value: 'qala', label: 'qala — Город' },
  { value: 'aul', label: 'aul — Аул' },
  { value: 'selo', label: 'selo — Село' },
]

interface FormState {
  kato_code: string
  name_ru: string
  name_kz: string
  type_code: string
  type_name_ru: string
  parent_code: string
  level: string
  is_active: boolean
  region_id: string
}

const EMPTY_FORM: FormState = {
  kato_code: '',
  name_ru: '',
  name_kz: '',
  type_code: '',
  type_name_ru: '',
  parent_code: '',
  level: '2',
  is_active: true,
  region_id: '',
}

function formFromItem(item: KatoItem): FormState {
  return {
    kato_code: item.kato_code,
    name_ru: item.name_ru,
    name_kz: item.name_kz ?? '',
    type_code: item.type_code ?? '',
    type_name_ru: item.type_name_ru ?? '',
    parent_code: item.parent_code ?? '',
    level: String(item.level),
    is_active: item.is_active,
    region_id: item.region_id ?? '',
  }
}

export default function ReferencesPage() {
  const router = useRouter()
  const { hasRole, user } = useAuthStore()

  const [activeTab, setActiveTab] = useState<TabKey>('kato')

  const [items, setItems] = useState<KatoItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [codeSearch, setCodeSearch] = useState('')
  const [filterLevel, setFilterLevel] = useState<string>('')
  const [isLoading, setIsLoading] = useState(false)

  const [showModal, setShowModal] = useState(false)
  const [editItem, setEditItem] = useState<KatoItem | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [modalError, setModalError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)

  const [deleteTarget, setDeleteTarget] = useState<KatoItem | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)

  const [regions, setRegions] = useState<Region[]>([])

  // Import modal state
  const [showImport, setShowImport] = useState(false)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importPhase, setImportPhase] = useState<'idle' | 'uploading' | 'running' | 'done' | 'error'>('idle')
  const [importJobId, setImportJobId] = useState<string | null>(null)
  const [importStatus, setImportStatus] = useState<KatoImportStatus | null>(null)
  const [showImportErrors, setShowImportErrors] = useState(false)
  const importPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Role guard: только после загрузки профиля — иначе при прямом открытии
  // страницы роль ещё пуста и администратора уводило на /dashboard.
  useEffect(() => {
    if (user && !hasRole('admin')) router.replace('/dashboard')
  }, [user, hasRole, router])

  // Load regions once
  useEffect(() => {
    fetchRegions().then(setRegions).catch(() => {})
  }, [])

  const loadItems = useCallback(async () => {
    setIsLoading(true)
    const params: { search?: string; code_search?: string; level?: number } = {}
    if (search.trim()) params.search = search.trim()
    if (codeSearch.trim()) params.code_search = codeSearch.trim()
    if (filterLevel) params.level = Number(filterLevel)
    const data = await fetchKatoList(params)
    setItems(data.items)
    setTotal(data.total)
    setIsLoading(false)
  }, [search, codeSearch, filterLevel])

  useEffect(() => {
    setPage(1)
    loadItems()
  }, [loadItems])

  // Client-side pagination
  const pageItems = items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE))

  // Modal helpers
  function openCreate() {
    setEditItem(null)
    setForm(EMPTY_FORM)
    setModalError(null)
    setShowModal(true)
  }

  function openEdit(item: KatoItem) {
    setEditItem(item)
    setForm(formFromItem(item))
    setModalError(null)
    setShowModal(true)
  }

  function closeModal() {
    setShowModal(false)
    setEditItem(null)
    setModalError(null)
  }

  async function handleSave() {
    if (!form.kato_code.trim() || !form.name_ru.trim() || !form.level) {
      setModalError('Заполните обязательные поля: Код КАТО, Наименование (рус.), Уровень')
      return
    }
    setIsSaving(true)
    setModalError(null)
    try {
      if (editItem) {
        const body: KatoUpdate = {
          name_ru: form.name_ru.trim(),
          name_kz: form.name_kz.trim() || null,
          type_code: form.type_code || null,
          type_name_ru: form.type_name_ru.trim() || null,
          parent_code: form.parent_code.trim() || null,
          level: Number(form.level),
          is_active: form.is_active,
          region_id: form.region_id || null,
        }
        await updateKato(editItem.kato_code, body)
      } else {
        const body: KatoCreate = {
          kato_code: form.kato_code.trim(),
          name_ru: form.name_ru.trim(),
          name_kz: form.name_kz.trim() || undefined,
          type_code: form.type_code || undefined,
          type_name_ru: form.type_name_ru.trim() || undefined,
          parent_code: form.parent_code.trim() || undefined,
          level: Number(form.level),
          is_active: form.is_active,
          region_id: form.region_id || null,
        }
        await createKato(body)
      }
      closeModal()
      loadItems()
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
      await deleteKato(deleteTarget.kato_code)
      setDeleteTarget(null)
      loadItems()
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Ошибка при удалении'
      setDeleteError(msg)
    } finally {
      setIsDeleting(false)
    }
  }

  function setF(field: keyof FormState, value: string | boolean) {
    setForm((prev) => ({ ...prev, [field]: value }))
  }

  function openImport() {
    setImportFile(null)
    setImportPhase('idle')
    setImportJobId(null)
    setImportStatus(null)
    setShowImportErrors(false)
    setShowImport(true)
  }

  function closeImport() {
    if (importPollRef.current) clearInterval(importPollRef.current)
    setShowImport(false)
    if (importPhase === 'done') loadItems()
  }

  async function startImport() {
    if (!importFile) return
    setImportPhase('uploading')
    try {
      const { job_id } = await importKatoCsv(importFile)
      setImportJobId(job_id)
      setImportPhase('running')
      const interval = setInterval(async () => {
        try {
          const st = await getKatoImportStatus(job_id)
          setImportStatus(st)
          if (st.status === 'done') {
            clearInterval(interval)
            importPollRef.current = null
            setImportPhase('done')
          } else if (st.status === 'error') {
            clearInterval(interval)
            importPollRef.current = null
            setImportPhase('error')
          }
        } catch {
          clearInterval(interval)
          importPollRef.current = null
          setImportPhase('error')
        }
      }, 2000)
      importPollRef.current = interval
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Ошибка при загрузке файла'
      setImportStatus({
        status: 'error',
        total: 0,
        processed: 0,
        added: 0,
        updated: 0,
        skipped: 0,
        errors: [],
        error_message: msg,
      })
      setImportPhase('error')
    }
  }

  const inputCls =
    'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500'
  const labelCls = 'block text-sm font-medium text-gray-700 mb-1'

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Справочники</h1>
        <p className="mt-1 text-sm text-gray-500">Управление классификаторами и нормативными данными</p>
      </div>

      {/* Tabs */}
      <div className="mb-6 border-b border-gray-200">
        <nav className="-mb-px flex gap-6">
          {([
            { key: 'kato', label: 'КАТО' },
            { key: 'indicators', label: 'Показатели' },
            { key: 'units', label: 'Единицы измерения' },
            { key: 'sectors', label: 'Секторы экономики' },
          ] as { key: TabKey; label: string }[]).map((t) => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={
                activeTab === t.key
                  ? 'border-b-2 border-primary-600 pb-3 text-sm font-semibold text-primary-600'
                  : 'border-b-2 border-transparent pb-3 text-sm font-medium text-gray-500 hover:text-gray-700'
              }
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>

      {activeTab === 'indicators' && <IndicatorsTab />}
      {activeTab === 'units' && <UnitsTab />}
      {activeTab === 'sectors' && <EconomicSectorsTab />}

      {activeTab === 'kato' && (
      <>
      {/* Toolbar */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input
          type="text"
          placeholder="Поиск по наименованию..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-64 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        />
        <input
          type="text"
          placeholder="Поиск по коду КАТО..."
          value={codeSearch}
          onChange={(e) => setCodeSearch(e.target.value)}
          className="w-48 rounded-lg border border-gray-300 px-3 py-2 text-sm font-mono focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        />
        <select
          value={filterLevel}
          onChange={(e) => setFilterLevel(e.target.value)}
          className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        >
          <option value="">Все уровни</option>
          <option value="1">Уровень 1 (Область)</option>
          <option value="2">Уровень 2 (Район/Город)</option>
          <option value="3">Уровень 3</option>
          <option value="4">Уровень 4</option>
          <option value="5">Уровень 5</option>
        </select>
        <div className="ml-auto flex items-center gap-2">
          {/* Кнопка для будущего развития */}
          <button
            onClick={openImport}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50"
          >
            Импорт из CSV
          </button>
          <button
            onClick={openCreate}
            className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700"
          >
            + Добавить запись
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              {['Код КАТО', 'Наименование (рус.)', 'Наименование (каз.)', 'Тип', 'Ур.', 'Родитель', 'Статус', ''].map(
                (h) => (
                  <th
                    key={h}
                    className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500"
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading ? (
              Array.from({ length: 6 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 8 }).map((__, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 rounded bg-gray-200 animate-pulse" />
                    </td>
                  ))}
                </tr>
              ))
            ) : pageItems.length === 0 ? (
              <tr>
                <td colSpan={8} className="py-10 text-center text-sm text-gray-400">
                  Записей не найдено
                </td>
              </tr>
            ) : (
              pageItems.map((item) => (
                <tr key={item.kato_code} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono font-medium text-gray-900">{item.kato_code}</td>
                  <td className="px-4 py-3 text-gray-900">{item.name_ru}</td>
                  <td className="px-4 py-3 text-gray-500">{item.name_kz ?? '—'}</td>
                  <td className="px-4 py-3">
                    {item.type_code ? (
                      <span className="inline-flex items-center rounded-md bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 ring-1 ring-blue-200">
                        {item.type_code}
                      </span>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center text-gray-700">{item.level}</td>
                  <td className="px-4 py-3 font-mono text-gray-500">{item.parent_code ?? '—'}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                        item.is_active
                          ? 'bg-green-50 text-green-700 ring-1 ring-green-200'
                          : 'bg-gray-100 text-gray-500 ring-1 ring-gray-200'
                      }`}
                    >
                      {item.is_active ? 'Активен' : 'Неактивен'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => openEdit(item)}
                        className="rounded-md px-2 py-1 text-xs font-medium text-primary-600 hover:bg-primary-50"
                      >
                        Изменить
                      </button>
                      <button
                        onClick={() => { setDeleteTarget(item); setDeleteError(null) }}
                        className="rounded-md px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
                      >
                        Удалить
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="mt-4 flex items-center justify-between text-sm text-gray-500">
        <span>
          Всего: {total} записей{items.length !== total ? ` (отфильтровано: ${items.length})` : ''}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="rounded-lg border border-gray-300 px-3 py-1 hover:bg-gray-50 disabled:opacity-40"
          >
            ←
          </button>
          <span>
            {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="rounded-lg border border-gray-300 px-3 py-1 hover:bg-gray-50 disabled:opacity-40"
          >
            →
          </button>
        </div>
      </div>

      {/* Create/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-5">
              <h3 className="text-base font-semibold text-gray-900">
                {editItem ? 'Редактировать запись КАТО' : 'Новая запись КАТО'}
              </h3>
              <button onClick={closeModal} className="text-gray-400 hover:text-gray-600 text-xl leading-none">
                ×
              </button>
            </div>

            <div className="px-6 py-5 space-y-4">
              {modalError && (
                <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
                  {modalError}
                </div>
              )}

              <div className="grid grid-cols-2 gap-4">
                {/* kato_code */}
                <div>
                  <label className={labelCls}>
                    Код КАТО <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={form.kato_code}
                    onChange={(e) => setF('kato_code', e.target.value)}
                    disabled={!!editItem}
                    maxLength={9}
                    placeholder="591000000"
                    className={inputCls + (editItem ? ' bg-gray-50 text-gray-400' : '')}
                  />
                </div>

                {/* level */}
                <div>
                  <label className={labelCls}>
                    Уровень <span className="text-red-500">*</span>
                  </label>
                  <select
                    value={form.level}
                    onChange={(e) => setF('level', e.target.value)}
                    className={inputCls}
                  >
                    {[1, 2, 3, 4, 5].map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </div>

                {/* name_ru */}
                <div className="col-span-2">
                  <label className={labelCls}>
                    Наименование (рус.) <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={form.name_ru}
                    onChange={(e) => setF('name_ru', e.target.value)}
                    maxLength={250}
                    placeholder="Северо-Казахстанская область"
                    className={inputCls}
                  />
                </div>

                {/* name_kz */}
                <div className="col-span-2">
                  <label className={labelCls}>Наименование (каз.)</label>
                  <input
                    type="text"
                    value={form.name_kz}
                    onChange={(e) => setF('name_kz', e.target.value)}
                    maxLength={250}
                    placeholder="Солтүстік Қазақстан облысы"
                    className={inputCls}
                  />
                </div>

                {/* type_code */}
                <div>
                  <label className={labelCls}>Тип объекта (код)</label>
                  <select
                    value={form.type_code}
                    onChange={(e) => setF('type_code', e.target.value)}
                    className={inputCls}
                  >
                    {TYPE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </div>

                {/* type_name_ru */}
                <div>
                  <label className={labelCls}>Тип объекта (наименование)</label>
                  <input
                    type="text"
                    value={form.type_name_ru}
                    onChange={(e) => setF('type_name_ru', e.target.value)}
                    maxLength={100}
                    placeholder="Область"
                    className={inputCls}
                  />
                </div>

                {/* parent_code */}
                <div>
                  <label className={labelCls}>Код родителя</label>
                  <input
                    type="text"
                    value={form.parent_code}
                    onChange={(e) => setF('parent_code', e.target.value)}
                    maxLength={9}
                    placeholder="591000000"
                    className={inputCls}
                  />
                </div>

                {/* region_id */}
                <div>
                  <label className={labelCls}>Регион системы</label>
                  <select
                    value={form.region_id}
                    onChange={(e) => setF('region_id', e.target.value)}
                    className={inputCls}
                  >
                    <option value="">— не привязан —</option>
                    {regions.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* is_active */}
                <div className="col-span-2">
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={form.is_active}
                      onChange={(e) => setF('is_active', e.target.checked)}
                      className="h-4 w-4 rounded accent-primary-600"
                    />
                    <span className="text-sm font-medium text-gray-700">Активен</span>
                  </label>
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
              <button
                onClick={closeModal}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                Отмена
              </button>
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:opacity-50"
              >
                {isSaving ? 'Сохранение...' : 'Сохранить'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Import Modal */}
      {showImport && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-5">
              <h3 className="text-base font-semibold text-gray-900">
                {importPhase === 'done' ? 'Импорт завершён' :
                 importPhase === 'error' ? 'Ошибка импорта' :
                 importPhase === 'running' ? 'Импорт выполняется...' :
                 'Импорт КАТО из CSV'}
              </h3>
              {(importPhase === 'idle' || importPhase === 'done' || importPhase === 'error') && (
                <button onClick={closeImport} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
              )}
            </div>

            <div className="px-6 py-5 space-y-4">
              {/* idle: file picker */}
              {importPhase === 'idle' && (
                <>
                  <p className="text-sm text-gray-500">
                    Формат: CSV, разделитель <code className="bg-gray-100 px-1 rounded">;</code>, кодировка UTF-8.<br />
                    Обязательные колонки: <code className="bg-gray-100 px-1 rounded">id, parent_id, code, name</code>
                  </p>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Файл CSV</label>
                    <input
                      type="file"
                      accept=".csv,text/csv"
                      onChange={(e) => setImportFile(e.target.files?.[0] ?? null)}
                      className="block w-full text-sm text-gray-600 file:mr-3 file:rounded-lg file:border file:border-gray-300 file:bg-gray-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-gray-700 hover:file:bg-gray-100"
                    />
                    {importFile && (
                      <p className="mt-1 text-xs text-gray-400">{importFile.name} ({(importFile.size / 1024).toFixed(0)} КБ)</p>
                    )}
                  </div>
                </>
              )}

              {/* uploading */}
              {importPhase === 'uploading' && (
                <div className="flex items-center gap-3 text-sm text-gray-600">
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-600 border-t-transparent" />
                  Загрузка файла...
                </div>
              )}

              {/* running: progress */}
              {importPhase === 'running' && importStatus && (
                <div className="space-y-3">
                  <div className="text-sm text-gray-700">
                    Обрабатывается:{' '}
                    <span className="font-semibold">{importStatus.processed.toLocaleString()}</span>
                    {' / '}
                    <span className="font-semibold">{importStatus.total.toLocaleString()}</span>
                  </div>
                  <div className="h-3 w-full overflow-hidden rounded-full bg-gray-200">
                    <div
                      className="h-full rounded-full bg-primary-600 transition-all duration-500"
                      style={{ width: importStatus.total > 0 ? `${Math.round((importStatus.processed / importStatus.total) * 100)}%` : '0%' }}
                    />
                  </div>
                  <div className="flex gap-4 text-xs text-gray-500">
                    <span>Добавлено: <b className="text-green-700">{importStatus.added}</b></span>
                    <span>Обновлено: <b className="text-blue-700">{importStatus.updated}</b></span>
                    <span>Пропущено: <b className="text-gray-700">{importStatus.skipped}</b></span>
                  </div>
                </div>
              )}

              {/* running: waiting for first status */}
              {importPhase === 'running' && !importStatus && (
                <div className="flex items-center gap-3 text-sm text-gray-600">
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-600 border-t-transparent" />
                  Инициализация...
                </div>
              )}

              {/* done */}
              {importPhase === 'done' && importStatus && (
                <div className="space-y-3">
                  <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 space-y-1">
                    <div className="flex justify-between text-sm">
                      <span className="text-gray-600">Добавлено</span>
                      <span className="font-semibold text-green-700">{importStatus.added.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-gray-600">Обновлено (name_ru)</span>
                      <span className="font-semibold text-blue-700">{importStatus.updated.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-gray-600">Без изменений</span>
                      <span className="font-semibold text-gray-700">{importStatus.skipped.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between text-sm border-t border-green-200 pt-1 mt-1">
                      <span className="text-gray-600">Всего в файле</span>
                      <span className="font-semibold text-gray-900">{importStatus.total.toLocaleString()}</span>
                    </div>
                  </div>
                  {importStatus.errors.length > 0 && (
                    <div>
                      <button
                        onClick={() => setShowImportErrors((v) => !v)}
                        className="text-sm text-amber-700 hover:underline"
                      >
                        {showImportErrors ? 'Скрыть' : 'Показать'} ошибки ({importStatus.errors.length})
                      </button>
                      {showImportErrors && (
                        <ul className="mt-2 max-h-32 overflow-y-auto rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800 space-y-0.5">
                          {importStatus.errors.map((e, i) => (
                            <li key={i}>{e}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* error */}
              {importPhase === 'error' && (
                <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
                  {importStatus?.error_message ?? 'Непредвиденная ошибка при импорте'}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
              {importPhase === 'idle' && (
                <>
                  <button
                    onClick={closeImport}
                    className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
                  >
                    Отмена
                  </button>
                  <button
                    onClick={startImport}
                    disabled={!importFile}
                    className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:opacity-50"
                  >
                    Загрузить
                  </button>
                </>
              )}
              {(importPhase === 'done' || importPhase === 'error') && (
                <button
                  onClick={closeImport}
                  className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700"
                >
                  Закрыть
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirm Modal */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="w-full max-w-sm rounded-2xl bg-white shadow-2xl">
            <div className="px-6 py-5">
              <h3 className="text-base font-semibold text-gray-900 mb-2">Удалить запись?</h3>
              <p className="text-sm text-gray-600">
                Код:{' '}
                <span className="font-mono font-medium text-gray-900">{deleteTarget.kato_code}</span>
                <br />
                {deleteTarget.name_ru}
              </p>
              {deleteError && (
                <div className="mt-3 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
                  {deleteError}
                </div>
              )}
            </div>
            <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
              <button
                onClick={() => { setDeleteTarget(null); setDeleteError(null) }}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                Отмена
              </button>
              <button
                onClick={handleDelete}
                disabled={isDeleting}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
              >
                {isDeleting ? 'Удаление...' : 'Удалить'}
              </button>
            </div>
          </div>
        </div>
      )}
      </>
      )}
    </div>
  )
}
