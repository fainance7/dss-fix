'use client'

import { useState, useEffect, useMemo } from 'react'
import { useAuthStore } from '@/store/authStore'
import {
  fetchTasks, updateTaskStatus, updateTask as updateTaskApi,
  fetchTaskEvents, addTaskComment, acceptTask, rejectTaskCompletion,
  fetchScenarios, onlyWorkingTasks,
} from '@/lib/api/ai'
import type { AiTask, TaskUpdate, TaskEvent } from '@/lib/api/ai'
import { fetchRegions, fetchUsersByRegion } from '@/lib/api/backend'
import type { Region, UserMinimal } from '@/lib/api/backend'

const SCENARIO_TYPE_RU: Record<string, string> = {
  optimal: 'Оптимальный',
  stable: 'Стабильный',
  risk: 'Рисковый',
  custom: 'Пользовательский',
}

const AI_STATUS_RU: Record<string, TaskStatus> = {
  pending: 'Новая',
  in_progress: 'В работе',
  completed: 'Выполнено',
  cancelled: 'Отменено',
  accepted: 'Принята',
}

const RU_TO_AI_STATUS: Record<string, string> = {
  'Новая': 'pending',
  'В работе': 'in_progress',
  'Выполнено': 'completed',
  'Отменено': 'cancelled',
  'Принята': 'accepted',
}

type TaskStatus = 'Новая' | 'В работе' | 'Выполнено' | 'Отменено' | 'Принята'

interface Task {
  id: string
  title: string
  district: string
  regionId: string
  status: TaskStatus
  deadline: string
  assigneeId: string | null
  description: string
  category: string
  scenarioType: string | null
  targetYear: number | null
  scenarioId: string
}

const STATUS_CONFIG: Record<TaskStatus, { color: string; bg: string }> = {
  'Новая':    { color: 'text-blue-700',   bg: 'bg-blue-100' },
  'В работе': { color: 'text-yellow-700', bg: 'bg-yellow-100' },
  'Выполнено':{ color: 'text-green-700',  bg: 'bg-green-100' },
  'Отменено': { color: 'text-gray-500',   bg: 'bg-gray-100' },
  'Принята':  { color: 'text-emerald-800', bg: 'bg-emerald-100' },
}

const STATUSES: TaskStatus[] = ['Новая', 'В работе', 'Выполнено', 'Принята', 'Отменено']

const EVENT_TYPE_LABEL: Record<string, string> = {
  comment: '💬 Комментарий',
  status_change: '🔄 Статус изменён',
  accept: '✅ Принята акиматом',
  reject_completion: '↩ Возвращена на доработку',
}

function mapAiTask(task: AiTask, regionMap: Record<string, string>): Task {
  return {
    id: task.id,
    title: task.title,
    district: regionMap[task.region_id] ?? '—',
    regionId: task.region_id,
    status: AI_STATUS_RU[task.status] ?? 'Новая',
    deadline: task.deadline ?? '',
    assigneeId: task.assigned_to,
    description: task.description ?? '',
    category: task.category ?? '',
    scenarioType: task.scenario_type,
    targetYear: task.target_year,
    scenarioId: task.scenario_id,
  }
}

function SkeletonRow() {
  return (
    <div className="rounded-xl border-l-4 border-l-gray-200 bg-white p-4 shadow-sm ring-1 ring-gray-100 animate-pulse">
      <div className="flex justify-between gap-3">
        <div className="flex-1 space-y-2">
          <div className="flex gap-2">
            <div className="h-3 w-24 rounded bg-gray-200" />
            <div className="h-3 w-16 rounded-full bg-gray-200" />
          </div>
          <div className="h-4 w-3/4 rounded bg-gray-200" />
          <div className="h-3 w-1/2 rounded bg-gray-200" />
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="h-3 w-20 rounded bg-gray-200" />
          <div className="h-3 w-16 rounded bg-gray-200" />
        </div>
      </div>
    </div>
  )
}

const ALL_DISTRICTS = 'Все районы'

export default function TasksPage() {
  const { hasRole, user } = useAuthStore()
  const isAkimRay = hasRole('akim_ray')
  const canEdit = hasRole('exp_dir') || hasRole('akim_obl') || hasRole('admin')
  const canAccept = hasRole('akim_obl') || hasRole('admin')
  // «Принята» ставится только приёмкой, отмена — решение области или эксперта (проверяется и на сервере).
  const canCancel = hasRole('akim_obl') || hasRole('exp_dir') || hasRole('admin')

  const [districts, setDistricts] = useState<string[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedDistrict, setSelectedDistrict] = useState(ALL_DISTRICTS)
  const [statusFilter, setStatusFilter] = useState<TaskStatus | 'Все'>('Все')
  const [search, setSearch] = useState('')

  // userMap: {region_id: UserMinimal[]} — кэш пользователей по региону
  const [userMap, setUserMap] = useState<Record<string, UserMinimal[]>>({})
  const [regionMap, setRegionMap] = useState<Record<string, string>>({})

  // Детальная модалка с историей
  const [detailTask, setDetailTask] = useState<Task | null>(null)
  const [taskEvents, setTaskEvents] = useState<TaskEvent[]>([])
  const [eventsLoading, setEventsLoading] = useState(false)
  const [commentText, setCommentText] = useState('')
  const [commentSending, setCommentSending] = useState(false)
  const [actionMode, setActionMode] = useState<'accept' | 'reject' | null>(null)
  const [actionComment, setActionComment] = useState('')
  const [actionSending, setActionSending] = useState(false)
  const [actionError, setActionError] = useState('')

  // Модальное окно обновления статуса
  const [updateModal, setUpdateModal] = useState<Task | null>(null)
  const [newStatus, setNewStatus] = useState<TaskStatus>('Новая')
  const [comment, setComment] = useState('')
  const [newAssigneeId, setNewAssigneeId] = useState<string>('')
  const [modalRegionUsers, setModalRegionUsers] = useState<UserMinimal[]>([])
  const [updating, setUpdating] = useState(false)
  const [updateError, setUpdateError] = useState('')

  // useEffect зависит от user — перезапускается когда user загружается (fetchMe в layout)
  useEffect(() => {
    loadAll()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user])

  async function loadAll() {
    setLoading(true)
    try {
      // Определяем регион для фильтрации akim_ray
      const rayRole = user?.roles.find((r) => r.name === 'akim_ray')
      const rayRegionId = rayRole?.region_id ?? null
      const rayRegionName = rayRole?.region_name ?? null

      const [regionsData, allTasks, scenarios] = await Promise.all([
        fetchRegions(),
        fetchTasks(rayRegionId ? { region_id: rayRegionId } : undefined),
        fetchScenarios(),
      ])
      const tasksData = onlyWorkingTasks(allTasks, scenarios)

      const rMap: Record<string, string> = {}
      regionsData.forEach((r: Region) => { rMap[r.id] = r.name })
      setRegionMap(rMap)

      const districtList = regionsData.filter((r: Region) => r.level === 2).map((r: Region) => r.name)
      setDistricts([ALL_DISTRICTS, ...districtList])

      const mappedTasks = tasksData.map((t: AiTask) => mapAiTask(t, rMap))
      setTasks(mappedTasks)

      // Предзагружаем пользователей для всех регионов задач с исполнителем
      const regionsWithAssignee = [...new Set(
        tasksData.filter((t: AiTask) => t.assigned_to).map((t: AiTask) => t.region_id)
      )]
      if (regionsWithAssignee.length > 0) {
        const userEntries = await Promise.all(
          regionsWithAssignee.map(async (rid: string) => {
            const users = await fetchUsersByRegion(rid)
            return [rid, users] as [string, typeof users]
          })
        )
        setUserMap(Object.fromEntries(userEntries))
      }

      // Для akim_ray: фиксируем фильтр на их район
      if (isAkimRay && rayRegionName) {
        setSelectedDistrict(rayRegionName)
      } else if (!isAkimRay) {
        // Переход с Дашборда: /dashboard/tasks?region=<id> — сразу фильтр по району.
        const fromUrl = new URLSearchParams(window.location.search).get('region')
        setSelectedDistrict(fromUrl && rMap[fromUrl] ? rMap[fromUrl] : ALL_DISTRICTS)
      }
    } catch {
      setTasks([])
    } finally {
      setLoading(false)
    }
  }

  async function openUpdateModal(task: Task) {
    setUpdateModal(task)
    setNewStatus(task.status)
    setNewAssigneeId(task.assigneeId ?? '')
    setComment('')
    setUpdateError('')

    // Подгружаем пользователей для региона задачи
    if (task.regionId) {
      const cached = userMap[task.regionId]
      if (cached) {
        setModalRegionUsers(cached)
      } else {
        const users = await fetchUsersByRegion(task.regionId)
        setUserMap((prev) => ({ ...prev, [task.regionId]: users }))
        setModalRegionUsers(users)
      }
    } else {
      setModalRegionUsers([])
    }
  }

  async function handleUpdateSubmit() {
    if (!updateModal) return
    setUpdating(true)
    setUpdateError('')
    try {
      const aiStatus = RU_TO_AI_STATUS[newStatus]
      // Если изменился исполнитель — делаем PATCH задачи
      if (canEdit && newAssigneeId !== (updateModal.assigneeId ?? '')) {
        const patchBody: TaskUpdate = {
          assigned_to: newAssigneeId || null,
          status: aiStatus,
          completion_report: comment || undefined,
        }
        await updateTaskApi(updateModal.id, patchBody)
      } else if (newStatus === updateModal.status) {
        // Статус не меняется — сохраняем только комментарий в истории задачи
        if (comment.trim()) await addTaskComment(updateModal.id, comment.trim(), user?.id, user?.username)
      } else {
        await updateTaskStatus(updateModal.id, aiStatus, comment || undefined)
      }
      setTasks((prev) =>
        prev.map((t) =>
          t.id === updateModal.id
            ? { ...t, status: newStatus, assigneeId: newAssigneeId || null }
            : t
        )
      )
      setUpdateModal(null)
    } catch {
      setUpdateError('Ошибка при обновлении. Попробуйте ещё раз.')
    } finally {
      setUpdating(false)
    }
  }

  const filtered = useMemo(() => {
    return tasks.filter((t) => {
      if (selectedDistrict !== ALL_DISTRICTS && t.district !== selectedDistrict) return false
      if (statusFilter !== 'Все' && t.status !== statusFilter) return false
      if (search && !t.title.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [tasks, selectedDistrict, statusFilter, search])

  const counts = useMemo(() => {
    const base = tasks.filter(
      (t) => selectedDistrict === ALL_DISTRICTS || t.district === selectedDistrict
    )
    return Object.fromEntries(
      STATUSES.map((s) => [s, base.filter((t) => t.status === s).length])
    ) as Record<TaskStatus, number>
  }, [tasks, selectedDistrict])

  const isOverdue = (deadline: string) =>
    deadline ? new Date(deadline) < new Date() : false

  function resolveAssignee(task: Task): string {
    if (!task.assigneeId) return ''
    const users = userMap[task.regionId] ?? []
    return users.find((u) => u.id === task.assigneeId)?.username ?? task.assigneeId.slice(0, 8) + '…'
  }

  async function openDetailModal(task: Task) {
    setDetailTask(task)
    setTaskEvents([])
    setCommentText('')
    setActionMode(null)
    setActionComment('')
    setActionError('')
    setEventsLoading(true)
    try {
      const events = await fetchTaskEvents(task.id)
      setTaskEvents(events)
    } finally {
      setEventsLoading(false)
    }
  }

  async function handleAddComment() {
    if (!detailTask || !commentText.trim()) return
    setCommentSending(true)
    try {
      const event = await addTaskComment(detailTask.id, commentText.trim(), user?.id, user?.username)
      setTaskEvents((prev) => [...prev, event])
      setCommentText('')
    } finally {
      setCommentSending(false)
    }
  }

  async function handleAccept() {
    if (!detailTask) return
    setActionSending(true)
    setActionError('')
    try {
      const updated = await acceptTask(detailTask.id, actionComment || undefined, user?.id, user?.username)
      setTasks((prev) => prev.map((t) => t.id === updated.id ? mapAiTask(updated, regionMap) : t))
      setDetailTask((prev) => prev ? { ...prev, status: AI_STATUS_RU[updated.status] ?? prev.status } : null)
      const events = await fetchTaskEvents(detailTask.id)
      setTaskEvents(events)
      setActionMode(null)
      setActionComment('')
    } catch {
      setActionError('Ошибка при принятии задачи. Попробуйте ещё раз.')
    } finally {
      setActionSending(false)
    }
  }

  async function handleRejectCompletion() {
    if (!detailTask || !actionComment.trim()) return
    setActionSending(true)
    setActionError('')
    try {
      const updated = await rejectTaskCompletion(detailTask.id, actionComment.trim(), user?.id, user?.username)
      setTasks((prev) => prev.map((t) => t.id === updated.id ? mapAiTask(updated, regionMap) : t))
      setDetailTask((prev) => prev ? { ...prev, status: AI_STATUS_RU[updated.status] ?? prev.status } : null)
      const events = await fetchTaskEvents(detailTask.id)
      setTaskEvents(events)
      setActionMode(null)
      setActionComment('')
    } catch {
      setActionError('Ошибка при отклонении задачи. Попробуйте ещё раз.')
    } finally {
      setActionSending(false)
    }
  }

  return (
    <>
      {/* Header */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Мероприятия</h1>
          <p className="mt-0.5 text-sm text-gray-500">
            {isAkimRay
              ? `Мероприятия вашего района: ${user?.roles.find((r) => r.name === 'akim_ray')?.region_name ?? ''}`
              : 'Управление мероприятиями районов СКО'}
          </p>
        </div>

        {!isAkimRay && (
          <select
            value={selectedDistrict}
            onChange={(e) => setSelectedDistrict(e.target.value)}
            className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value={ALL_DISTRICTS}>Все районы</option>
            {districts.filter((d) => d !== ALL_DISTRICTS).map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        )}
      </div>

      {/* Status counters */}
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {STATUSES.map((s) => {
          const cfg = STATUS_CONFIG[s]
          return (
            <button
              key={s}
              onClick={() => setStatusFilter(statusFilter === s ? 'Все' : s)}
              className={`rounded-xl bg-white p-4 text-left shadow-sm ring-1 ring-gray-100 transition-all hover:shadow-md ${
                statusFilter === s ? 'ring-2 ring-primary-500' : ''
              }`}
            >
              <div className="text-2xl font-bold text-gray-900">
                {loading ? (
                  <span className="inline-block h-7 w-6 rounded bg-gray-200 animate-pulse" />
                ) : (
                  counts[s] ?? 0
                )}
              </div>
              <div className="mt-1">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${cfg.bg} ${cfg.color}`}>
                  {s}
                </span>
              </div>
            </button>
          )
        })}
      </div>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap gap-3">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Поиск по названию..."
          className="flex-1 min-w-[200px] rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
        />
        {(statusFilter !== 'Все' || search) && (
          <button
            onClick={() => { setStatusFilter('Все'); setSearch('') }}
            className="rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50"
          >
            Сбросить
          </button>
        )}
      </div>

      {/* Task cards */}
      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => <SkeletonRow key={i} />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-xl bg-white py-16 text-center shadow-sm ring-1 ring-gray-100">
          <p className="text-gray-400">Мероприятия не найдены</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((task) => {
            const cfg = STATUS_CONFIG[task.status]
            const overdue = isOverdue(task.deadline) && task.status !== 'Выполнено' && task.status !== 'Принята' && task.status !== 'Отменено'
            const scenarioLabel = task.scenarioType
              ? `${SCENARIO_TYPE_RU[task.scenarioType] ?? task.scenarioType}${task.targetYear ? ` ${task.targetYear}` : ''}`
              : null
            const assigneeName = resolveAssignee(task)
            const borderColor =
              task.status === 'Выполнено' ? 'border-l-green-400' :
              task.status === 'В работе'  ? 'border-l-yellow-400' :
              task.status === 'Отменено'  ? 'border-l-gray-300' : 'border-l-blue-400'

            return (
              <div
                key={task.id}
                className={`rounded-xl border-l-4 bg-white p-4 shadow-sm ring-1 ring-gray-100 hover:shadow-md transition-shadow ${borderColor}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div
                    className="flex-1 min-w-0 cursor-pointer"
                    onClick={() => openDetailModal(task)}
                  >
                    {/* Сценарий + статус */}
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      {scenarioLabel && (
                        <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-600">
                          {scenarioLabel}
                        </span>
                      )}
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${cfg.bg} ${cfg.color}`}>
                        {task.status}
                      </span>
                      {task.category && (
                        <span className="text-xs text-gray-400">{task.category}</span>
                      )}
                    </div>
                    <h3 className="font-medium text-gray-900 hover:text-primary-600 transition-colors">{task.title}</h3>
                    {task.description && (
                      <p className="mt-1 text-sm text-gray-500 line-clamp-1">{task.description}</p>
                    )}
                    {/* Исполнитель */}
                    <div className="mt-2 flex items-center gap-3 text-xs text-gray-400">
                      <span>&#128205; {task.district}</span>
                      {assigneeName && (
                        <span>&#128100; {assigneeName}</span>
                      )}
                      {!assigneeName && (
                        <span className="text-orange-400">Исполнитель не назначен</span>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-col items-end gap-2 flex-shrink-0">
                    {task.deadline && (
                      <span className={`text-xs ${overdue ? 'text-red-600 font-semibold' : 'text-gray-400'}`}>
                        {overdue ? '⚠ ' : ''}
                        {new Date(task.deadline).toLocaleDateString('ru-RU')}
                      </span>
                    )}
                    {task.status === 'Выполнено' && canAccept ? (
                      <button
                        onClick={() => openDetailModal(task)}
                        className="rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700 transition-colors"
                      >
                        Принять / Отклонить
                      </button>
                    ) : task.status !== 'Отменено' && task.status !== 'Принята' && (
                      <button
                        onClick={() => openUpdateModal(task)}
                        className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700 transition-colors"
                      >
                        Обновить
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Detail modal — история + комментарии + принять/отклонить */}
      {detailTask && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-lg rounded-xl bg-white shadow-2xl flex flex-col max-h-[90vh]">

            {/* Header */}
            <div className="flex items-start justify-between border-b border-gray-100 px-6 py-4 flex-shrink-0">
              <div className="min-w-0 flex-1 pr-4">
                <div className="flex flex-wrap gap-2 mb-1">
                  {detailTask.scenarioType && (
                    <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-600">
                      {SCENARIO_TYPE_RU[detailTask.scenarioType] ?? detailTask.scenarioType}{detailTask.targetYear ? ` ${detailTask.targetYear}` : ''}
                    </span>
                  )}
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_CONFIG[detailTask.status]?.bg ?? 'bg-gray-100'} ${STATUS_CONFIG[detailTask.status]?.color ?? 'text-gray-600'}`}>
                    {detailTask.status}
                  </span>
                </div>
                <h3 className="font-semibold text-gray-900 leading-snug">{detailTask.title}</h3>
                <p className="mt-0.5 text-xs text-gray-400">
                  📍 {detailTask.district}
                  {resolveAssignee(detailTask) && ` · 👤 ${resolveAssignee(detailTask)}`}
                  {detailTask.deadline && ` · ${new Date(detailTask.deadline).toLocaleDateString('ru-RU')}`}
                </p>
              </div>
              <button onClick={() => setDetailTask(null)} className="text-xl leading-none text-gray-400 hover:text-gray-700 flex-shrink-0">×</button>
            </div>

            {/* Body: timeline */}
            <div className="overflow-y-auto flex-1 px-6 py-4 space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">Хронология</p>

              {eventsLoading && (
                <div className="space-y-2">
                  {[1, 2].map((i) => <div key={i} className="h-10 rounded bg-gray-100 animate-pulse" />)}
                </div>
              )}

              {!eventsLoading && taskEvents.length === 0 && (
                <p className="text-sm text-gray-400">История событий пока пуста</p>
              )}

              {taskEvents.map((ev) => (
                <div key={ev.id} className="flex gap-3">
                  <div className="mt-0.5 flex-shrink-0 w-2 h-2 rounded-full bg-primary-400 mt-1.5" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-medium text-gray-700">
                        {EVENT_TYPE_LABEL[ev.event_type] ?? ev.event_type}
                      </span>
                      {ev.old_status && ev.new_status && (
                        <span className="text-xs text-gray-400">
                          {AI_STATUS_RU[ev.old_status] ?? ev.old_status} → {AI_STATUS_RU[ev.new_status] ?? ev.new_status}
                        </span>
                      )}
                      {ev.author_name && (
                        <span className="text-xs text-gray-400">· {ev.author_name}</span>
                      )}
                      <span className="text-xs text-gray-300">
                        {new Date(ev.created_at).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>
                    {ev.comment && (
                      <p className="mt-0.5 text-sm text-gray-600 whitespace-pre-line">{ev.comment}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* Comment input (all roles) */}
            <div className="border-t border-gray-100 px-6 py-3 flex-shrink-0">
              <p className="mb-1.5 text-xs font-medium text-gray-500">Добавить комментарий</p>
              <div className="flex gap-2">
                <textarea
                  value={commentText}
                  onChange={(e) => setCommentText(e.target.value)}
                  rows={2}
                  placeholder="Введите комментарий..."
                  className="flex-1 rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-primary-500 focus:outline-none resize-none"
                />
                <button
                  onClick={handleAddComment}
                  disabled={commentSending || !commentText.trim()}
                  className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-50 self-end"
                >
                  {commentSending ? '...' : 'Отправить'}
                </button>
              </div>
            </div>

            {/* Accept / Reject (akim_obl when task is completed) */}
            {canAccept && (detailTask.status === 'Выполнено') && (
              <div className="border-t border-gray-100 px-6 py-3 flex-shrink-0 bg-green-50/50">
                {actionMode === null && (
                  <div className="flex gap-2">
                    <button
                      onClick={() => { setActionMode('reject'); setActionError('') }}
                      className="flex-1 rounded-lg border border-red-200 py-2 text-sm font-medium text-red-600 hover:bg-red-50"
                    >
                      Отклонить
                    </button>
                    <button
                      onClick={() => { setActionMode('accept'); setActionError('') }}
                      className="flex-1 rounded-lg bg-green-600 py-2 text-sm font-medium text-white hover:bg-green-700"
                    >
                      ✓ Принять
                    </button>
                  </div>
                )}
                {actionMode !== null && (
                  <div className="space-y-2">
                    {actionError && (
                      <p className="text-xs text-red-600">{actionError}</p>
                    )}
                    <textarea
                      value={actionComment}
                      onChange={(e) => setActionComment(e.target.value)}
                      rows={2}
                      placeholder={actionMode === 'accept' ? 'Комментарий (необязательно)' : 'Причина отклонения (обязательно)'}
                      className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:outline-none resize-none"
                      autoFocus
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={() => { setActionMode(null); setActionComment(''); setActionError('') }}
                        className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
                      >
                        Отмена
                      </button>
                      <button
                        onClick={actionMode === 'accept' ? handleAccept : handleRejectCompletion}
                        disabled={actionSending || (actionMode === 'reject' && !actionComment.trim())}
                        className={`flex-1 rounded-lg py-1.5 text-xs font-medium text-white disabled:opacity-50 ${actionMode === 'accept' ? 'bg-green-600 hover:bg-green-700' : 'bg-red-600 hover:bg-red-700'}`}
                      >
                        {actionSending ? 'Сохраняю...' : actionMode === 'accept' ? 'Подтвердить принятие' : 'Подтвердить отклонение'}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Footer */}
            <div className="border-t border-gray-100 px-6 py-3 text-right flex-shrink-0">
              <button onClick={() => setDetailTask(null)} className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
                Закрыть
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Update modal */}
      {updateModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-md rounded-xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <h3 className="font-semibold text-gray-900">Обновить задачу</h3>
              <button
                onClick={() => setUpdateModal(null)}
                className="text-xl leading-none text-gray-400 hover:text-gray-700"
              >×</button>
            </div>

            <div className="p-6 space-y-4">
              {updateError && (
                <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  {updateError}
                </div>
              )}

              <p className="text-sm text-gray-600">
                <span className="font-medium text-gray-900">{updateModal.title}</span>
                {updateModal.district !== '—' && (
                  <span className="ml-2 text-xs text-gray-400">&#128205; {updateModal.district}</span>
                )}
              </p>

              {/* Статус */}
              <div>
                <p className="mb-2 text-sm font-medium text-gray-700">Статус</p>
                <div className="grid grid-cols-2 gap-2">
                  {STATUSES.filter((s) => s !== updateModal.status && s !== 'Новая' && s !== 'Принята' && (s !== 'Отменено' || canCancel)).map((s) => {
                    const cfg = STATUS_CONFIG[s]
                    return (
                      <label
                        key={s}
                        className={`flex cursor-pointer items-center gap-2 rounded-lg border-2 p-3 transition-colors ${
                          newStatus === s ? 'border-primary-500 bg-primary-50' : 'border-gray-200 hover:border-gray-300'
                        }`}
                      >
                        <input
                          type="radio"
                          name="newStatus"
                          value={s}
                          checked={newStatus === s}
                          onChange={() => setNewStatus(s)}
                          className="sr-only"
                        />
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${cfg.bg} ${cfg.color}`}>{s}</span>
                      </label>
                    )
                  })}
                </div>
              </div>

              {/* Исполнитель */}
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">
                  Исполнитель
                </label>
                {!canEdit ? (
                  <p className="text-sm text-gray-600">
                    {modalRegionUsers.find((u) => u.id === updateModal.assigneeId)?.username ?? 'Не назначен'}
                    <span className="ml-2 text-xs text-gray-400">назначает акимат области</span>
                  </p>
                ) : modalRegionUsers.length > 0 ? (
                  <select
                    value={newAssigneeId}
                    onChange={(e) => setNewAssigneeId(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none"
                  >
                    <option value="">— Не назначен —</option>
                    {modalRegionUsers.map((u) => (
                      <option key={u.id} value={u.id}>{u.username}</option>
                    ))}
                  </select>
                ) : (
                  <p className="text-xs text-gray-400">Нет пользователей в данном регионе</p>
                )}
              </div>

              {/* Комментарий / отчёт */}
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">
                  {newStatus === 'Выполнено' ? 'Отчёт о выполнении' : 'Комментарий'}
                </label>
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  rows={newStatus === 'Выполнено' ? 4 : 2}
                  placeholder={newStatus === 'Выполнено' ? 'Опишите выполненные работы...' : 'Опишите изменение...'}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>
            </div>

            <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
              <button
                onClick={() => setUpdateModal(null)}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                Отмена
              </button>
              <button
                onClick={handleUpdateSubmit}
                disabled={updating}
                className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-60"
              >
                {updating ? 'Сохраняю...' : 'Сохранить'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
