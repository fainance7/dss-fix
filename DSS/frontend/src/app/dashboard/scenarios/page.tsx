'use client'

import { useState, useEffect } from 'react'
import { useAuthStore } from '@/store/authStore'
import {
  fetchScenarios,
  generateScenarios,
  approveScenario as approveScenarioApi,
  rejectScenario as rejectScenarioApi,
  fetchTasks,
  createTask,
  updateTask,
  deleteTask,
  patchScenarioParameters,
  recalculateScenario,
  startNarrative,
  startAllNarratives,
  getNarrativeStatus,
} from '@/lib/api/ai'
import type { Scenario, AiTask, ScenarioParameters, TaskCreate, TaskUpdate, NarrativeEntry } from '@/lib/api/ai'
import { fetchRegions } from '@/lib/api/backend'
import ScenarioLandscapeBlock from '@/components/landscape/ScenarioLandscapeBlock'
import type { ScenarioLandscapeSnapshot } from '@/lib/api/landscape'
import type { Region } from '@/lib/api/backend'

type ScenarioType = 'optimal' | 'stable' | 'risk'
type TabFilter = 'all' | 'current' | 'history'

interface DisplayScenario {
  id: string
  type: ScenarioType
  label: string
  description: string
  vrp: string
  employment: string
  population: string
  investment: string
  status: 'active' | 'approved' | 'draft' | 'archived'
  createdAt: string
  targetYear: number
  approvedBy?: string
  approvedAt?: string
  rejectionReason?: string
  approvalComment?: string
  scenarioParams: ScenarioParameters
  regionId: string
  narrative?: string | null
  narrativeGeneratedAt?: string | null
  // Нарративы по направлениям (direction_code → запись).
  narratives?: Record<string, NarrativeEntry> | null
  // Вариант ландшафтного планирования (снимок зонирования, см. ScenarioLandscapeBlock).
  landscape?: ScenarioLandscapeSnapshot | null
}

const SCENARIO_STYLE: Record<ScenarioType, { border: string; badge: string; badgeText: string; icon: string }> = {
  optimal: {
    border: 'border-t-green-500',
    badge: 'bg-green-100 text-green-700',
    badgeText: 'Оптимальный',
    icon: '↗',
  },
  stable: {
    border: 'border-t-blue-500',
    badge: 'bg-blue-100 text-blue-700',
    badgeText: 'Стабильный',
    icon: '→',
  },
  risk: {
    border: 'border-t-red-500',
    badge: 'bg-red-100 text-red-700',
    badgeText: 'Рисковый',
    icon: '↘',
  },
}

// Направления, по которым можно генерировать нарратив (code → подпись).
// Нарратив каждого направления сохраняется отдельно (карта narratives).
const NARRATIVE_DIRECTIONS: { code: string; label: string }[] = [
  { code: 'economy', label: 'Экономика' },
  { code: 'agriculture', label: 'Сельское хозяйство' },
  { code: 'land', label: 'Земельные ресурсы' },
  { code: 'social_demog', label: 'Соц.-демография' },
  { code: 'labor', label: 'Труд. ресурсы' },
  { code: 'climate', label: 'Климат' },
  { code: 'ecology', label: 'Экология' },
  { code: 'tourism', label: 'Туризм' },
  { code: 'transport', label: 'Транспорт' },
  { code: 'water', label: 'Вода' },
]

const STATUS_LABEL: Record<DisplayScenario['status'], { label: string; color: string }> = {
  active: { label: 'Активный', color: 'bg-primary-100 text-primary-700' },
  approved: { label: 'Утверждён', color: 'bg-green-100 text-green-700' },
  draft: { label: 'Отклонён', color: 'bg-red-100 text-red-600' },
  archived: { label: 'Архивный', color: 'bg-gray-100 text-gray-500' },
}

// KPI сценариев переопределены на реальные показатели из indicator_values (АПК +
// демография СКО). Слоты vrp/employment/population/investment сохранены как
// контейнеры отображения; их смысл задают подписи ниже.
const COMPARE_METRICS = [
  { label: 'Валовая продукция СХ, млрд ₸', key: 'vrp' as const },
  { label: 'Естественный прирост, чел.', key: 'employment' as const },
  { label: 'Население, тыс. чел.', key: 'population' as const },
  { label: 'Инвестиции в АПК, млрд ₸', key: 'investment' as const },
]

// Форматирует значение KPI в строку, пригодную для parseFloat (число в начале,
// без разделителей тысяч). Возвращает '—' при отсутствии значения.
function fmtNum(v: number | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toFixed(digits)
}

const TABS: { id: TabFilter; label: string }[] = [
  { id: 'all', label: 'Все' },
  { id: 'current', label: 'Текущий' },
  { id: 'history', label: 'История' },
]

const SCENARIO_TYPE_LABELS: Record<ScenarioType, string> = {
  optimal: 'Оптимальный',
  stable: 'Стабильный',
  risk: 'Рисковый',
}

const SCENARIO_DESCRIPTIONS: Record<ScenarioType, string> = {
  optimal:
    'Максимальное использование потенциала агросектора, привлечение инвестиций, рост занятости. Реализуется при благоприятной конъюнктуре рынка и полном финансировании.',
  stable:
    'Сохранение текущих темпов развития. Умеренный рост ВРП, стабильный уровень занятости. Базовый сценарий для бюджетного планирования.',
  risk:
    'Неблагоприятный сценарий при засухе, падении цен на зерно и сокращении субсидий. Требует разработки антикризисных мер.',
}

function mapApiScenario(sc: Scenario): DisplayScenario {
  // Новый набор KPI из реальных данных: agri_output, natural_increase,
  // population, agri_investment (см. ai-service scenarios.py). Раскладываем их по
  // слотам отображения vrp/employment/population/investment.
  const kpis = (sc.parameters?.kpis ?? {}) as {
    agri_output?: number
    natural_increase?: number
    population?: number
    agri_investment?: number
  }
  const agri = kpis.agri_output
  const natInc = kpis.natural_increase
  const pop = kpis.population
  const invest = kpis.agri_investment

  const savedParams = (sc.parameters?.scenario_params ?? {}) as Record<string, number>

  return {
    id: sc.id,
    type: sc.scenario_type,
    label: SCENARIO_TYPE_LABELS[sc.scenario_type],
    description: SCENARIO_DESCRIPTIONS[sc.scenario_type],
    vrp: agri != null ? `${fmtNum(agri)} млрд ₸` : '—',
    employment: natInc != null ? `${Math.round(natInc)} чел.` : '—',
    population: pop != null ? `${fmtNum(pop / 1000)} тыс.` : '—',
    investment: invest != null ? `${fmtNum(invest)} млрд ₸` : '—',
    status: sc.status === 'approved' ? 'approved'
      : sc.status === 'archived' ? 'archived'
      : sc.status === 'calculated' ? 'active'
      : sc.status === 'rejected' ? 'draft'
      : 'draft',
    createdAt: sc.created_at,
    targetYear: sc.target_year,
    rejectionReason: sc.parameters?.rejection_reason as string | undefined,
    approvalComment: sc.parameters?.approval_comment as string | undefined,
    // Кто и когда утвердил (ФFE.4): имя фиксируется при утверждении.
    approvedBy: sc.approved_at
      ? ((sc.parameters?.approved_by_name as string | undefined) ?? 'аким области')
      : undefined,
    approvedAt: sc.approved_at ?? undefined,
    scenarioParams: {
      investment_multiplier: savedParams.investment_multiplier ?? 1.0,
      population_growth_rate: savedParams.population_growth_rate ?? 0.01,
      infrastructure_index: savedParams.infrastructure_index ?? 1.0,
      employment_target: savedParams.employment_target ?? 0.95,
      gdp_growth_target: savedParams.gdp_growth_target ?? 0.03,
    },
    regionId: sc.region_id,
    narrative: sc.narrative ?? null,
    narrativeGeneratedAt: sc.narrative_generated_at ?? null,
    narratives: sc.narratives ?? null,
    landscape: (sc.parameters?.landscape as ScenarioLandscapeSnapshot | undefined) ?? null,
  }
}

function SkeletonCard() {
  return (
    <div className="rounded-xl border-t-4 border-gray-200 bg-white p-5 shadow-sm ring-1 ring-gray-100 animate-pulse">
      <div className="mb-3 flex gap-2">
        <div className="h-6 w-6 rounded bg-gray-200" />
        <div className="h-5 w-24 rounded-full bg-gray-200" />
      </div>
      <div className="mb-4 space-y-1.5">
        <div className="h-3 w-full rounded bg-gray-200" />
        <div className="h-3 w-4/5 rounded bg-gray-200" />
        <div className="h-3 w-3/5 rounded bg-gray-200" />
      </div>
      <div className="space-y-2 rounded-lg bg-gray-50 p-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex justify-between">
            <div className="h-3 w-24 rounded bg-gray-200" />
            <div className="h-3 w-16 rounded bg-gray-200" />
          </div>
        ))}
      </div>
    </div>
  )
}


export default function ScenariosPage() {
  const { hasRole, user } = useAuthStore()
  const canApprove = hasRole('akim_obl') || hasRole('admin')
  const canGenerate = hasRole('exp_dir') || hasRole('admin')
  const canLinkLandscape = canGenerate || hasRole('akim_obl')
  // Аким области корректирует параметры до утверждения и ведёт задачи (ТЗ, Прил. 2, ч. 3).
  const canEditParams = canLinkLandscape
  const canManageTasks = canLinkLandscape

  const [tab, setTab] = useState<TabFilter>('all')
  const [scenarios, setScenarios] = useState<DisplayScenario[]>([])
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [targetYear, setTargetYear] = useState(2030)
  const [approvingId, setApprovingId] = useState<string | null>(null)
  const [approveTarget, setApproveTarget] = useState<DisplayScenario | null>(null)
  const [approvalComment, setApprovalComment] = useState('')
  const [approveError, setApproveError] = useState('')
  const [generateError, setGenerateError] = useState('')

  // Reject
  const [rejectTarget, setRejectTarget] = useState<DisplayScenario | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [rejectError, setRejectError] = useState('')
  const [rejecting, setRejecting] = useState(false)

  // Tasks per scenario
  const [tasksModal, setTasksModal] = useState<{ id: string; label: string } | null>(null)
  const [tasks, setTasks] = useState<AiTask[]>([])
  const [tasksLoading, setTasksLoading] = useState(false)

  // Parameters panel
  const [paramsTarget, setParamsTarget] = useState<DisplayScenario | null>(null)
  const [params, setParams] = useState<ScenarioParameters>({})
  const [recalculating, setRecalculating] = useState(false)

  // Narrative (autonomous local LLM)
  const [narrativeTarget, setNarrativeTarget] = useState<DisplayScenario | null>(null)
  const [narrativeText, setNarrativeText] = useState<string>('')
  const [narrativeGenerating, setNarrativeGenerating] = useState(false)
  const [narrativeError, setNarrativeError] = useState('')
  // Спокойное информационное сообщение (не ошибка) — например «генерация идёт долго».
  const [narrativeNotice, setNarrativeNotice] = useState('')
  const [narrativeDirection, setNarrativeDirection] = useState<string>('economy')
  // Батч-генерация «все направления» идёт (последовательно на сервере).
  const [narrativeBatch, setNarrativeBatch] = useState(false)

  // Tasks for "Текущий" tab (approved scenarios)
  const [currentTasks, setCurrentTasks] = useState<AiTask[]>([])
  const [currentTasksLoading, setCurrentTasksLoading] = useState(false)

  // Regions for task form
  const [regions, setRegions] = useState<Region[]>([])

  // Oblast-level region (level=1) — loaded from API, not hardcoded
  const skoRegionId = regions.find((r) => r.level === 1)?.id ?? ''

  // Task CRUD
  const [taskForm, setTaskForm] = useState<{ mode: 'create' | 'edit'; task?: AiTask } | null>(null)
  const [taskFormData, setTaskFormData] = useState({ title: '', category: '', deadline: '', status: 'pending', region_id: '' })
  const [taskSaving, setTaskSaving] = useState(false)

  useEffect(() => {
    loadScenarios()
    fetchRegions().then(setRegions).catch(() => {})
  }, [])

  useEffect(() => {
    if (tab !== 'current') return
    const approved = scenarios.filter((s) => s.status === 'approved')
    if (approved.length === 0) { setCurrentTasks([]); return }
    setCurrentTasksLoading(true)
    Promise.all(approved.map((s) => fetchTasks({ scenario_id: s.id })))
      .then((results) => setCurrentTasks(results.flat()))
      .catch(() => setCurrentTasks([]))
      .finally(() => setCurrentTasksLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, scenarios])

  async function loadScenarios() {
    setLoading(true)
    try {
      const data = await fetchScenarios()
      setScenarios(data.map(mapApiScenario))
    } catch {
      setScenarios([])
    } finally {
      setLoading(false)
    }
  }

  async function handleGenerate() {
    setGenerating(true)
    setGenerateError('')
    try {
      const userId = user?.id ?? ''
      const data = await generateScenarios(skoRegionId, targetYear, userId)
      setScenarios(data.map(mapApiScenario))
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Ошибка генерации сценариев. Проверьте, что AI-сервис запущен.'
      setGenerateError(msg)
    } finally {
      setGenerating(false)
    }
  }

  async function handleApprove() {
    if (!approveTarget) return
    setApprovingId(approveTarget.id)
    setApproveError('')
    try {
      const userId = user?.id ?? ''
      await approveScenarioApi(approveTarget.id, userId, approvalComment)
      await loadScenarios()
      setApproveTarget(null)
      setApprovalComment('')
    } catch {
      setApproveError('Ошибка при утверждении. Попробуйте ещё раз.')
    } finally {
      setApprovingId(null)
    }
  }

  async function handleReject() {
    if (!rejectTarget) return
    setRejecting(true)
    setRejectError('')
    try {
      const userId = user?.id ?? ''
      await rejectScenarioApi(rejectTarget.id, rejectReason, userId)
      await loadScenarios()
      setRejectTarget(null)
      setRejectReason('')
    } catch {
      setRejectError('Ошибка при отклонении. Попробуйте ещё раз.')
    } finally {
      setRejecting(false)
    }
  }

  async function handleOpenTasks(sc: DisplayScenario) {
    setTasksModal({ id: sc.id, label: SCENARIO_STYLE[sc.type].badgeText })
    setTasksLoading(true)
    setTasks([])
    try {
      const data = await fetchTasks({ scenario_id: sc.id })
      setTasks(data)
    } finally {
      setTasksLoading(false)
    }
  }

  function handleOpenParams(sc: DisplayScenario) {
    setParamsTarget(sc)
    setParams({ ...sc.scenarioParams })
  }

  // Записать результат по направлению в локальную карту narratives сценария —
  // чтобы переключение направления показывало сохранённый текст без перезапроса.
  function mergeNarrative(scenarioId: string, direction: string, entry: NarrativeEntry) {
    setScenarios((prev) =>
      prev.map((s) =>
        s.id === scenarioId
          ? { ...s, narratives: { ...(s.narratives ?? {}), [direction]: entry } }
          : s
      )
    )
    setNarrativeTarget((prev) =>
      prev && prev.id === scenarioId
        ? { ...prev, narratives: { ...(prev.narratives ?? {}), [direction]: entry } }
        : prev
    )
  }

  // Опрос статуса фоновой генерации по конкретному направлению до готовности/
  // ошибки. Используется и при старте, и при повторном открытии окна.
  async function pollNarrative(scenarioId: string, direction: string) {
    setNarrativeGenerating(true)
    setNarrativeError('')
    setNarrativeNotice('')
    const started = Date.now()
    try {
      for (;;) {
        const st = await getNarrativeStatus(scenarioId, direction)
        if (st.narrative_status === 'done') {
          setNarrativeNotice('')
          const text = st.narrative ?? ''
          // Показываем текст только если пользователь всё ещё на этом направлении.
          if (narrativeDirection === direction) setNarrativeText(text)
          mergeNarrative(scenarioId, direction, {
            text,
            status: 'done',
            generated_at: st.narrative_generated_at ?? null,
          })
          return
        }
        if (st.narrative_status === 'failed') {
          setNarrativeError(st.narrative_error ?? 'Не удалось получить оценку и прогноз ИИ')
          mergeNarrative(scenarioId, direction, {
            status: 'failed',
            error: st.narrative_error ?? null,
          })
          return
        }
        // Всё ещё идёт: это НЕ ошибка. На слабом CPU отчёт готовится долго
        // (десятки минут) — показываем спокойное уведомление и продолжаем опрос.
        const mins = Math.floor((Date.now() - started) / 60000)
        setNarrativeNotice(
          `Генерация идёт${mins >= 1 ? ` (${mins} мин)` : ''}. На сервере без GPU это ` +
            'может занять до ~40 минут. Можно закрыть окно — текст сохранится ' +
            'автоматически и появится здесь при следующем открытии.'
        )
        await new Promise((r) => setTimeout(r, 10000))
      }
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } }; message?: string })
          ?.response?.data?.detail ??
        (err as { message?: string })?.message ??
        'Ошибка опроса статуса генерации'
      setNarrativeError(message)
    } finally {
      setNarrativeGenerating(false)
    }
  }

  // Показать сохранённый нарратив выбранного направления (и возобновить опрос,
  // если для него генерация ещё идёт). Вызывается при открытии окна и при смене
  // направления в выпадающем списке.
  async function showNarrativeForDirection(sc: DisplayScenario, direction: string) {
    const entry = sc.narratives?.[direction]
    setNarrativeText(entry?.text ?? '')
    setNarrativeError(entry?.status === 'failed' ? (entry.error ?? '') : '')
    setNarrativeNotice('')
    try {
      const st = await getNarrativeStatus(sc.id, direction)
      if (st.narrative_status === 'done') {
        setNarrativeText(st.narrative ?? '')
        mergeNarrative(sc.id, direction, {
          text: st.narrative ?? '',
          status: 'done',
          generated_at: st.narrative_generated_at ?? null,
        })
      } else if (st.narrative_status === 'running') {
        // Уже идёт (запущено ранее, окно переоткрыли) — возобновляем опрос,
        // показываем спокойное «идёт», а не ошибку.
        void pollNarrative(sc.id, direction)
      } else if (st.narrative_status === 'idle') {
        setNarrativeText('')
      }
    } catch {
      /* статус — best-effort */
    }
  }

  function handleOpenNarrative(sc: DisplayScenario) {
    setNarrativeTarget(sc)
    setNarrativeError('')
    void showNarrativeForDirection(sc, narrativeDirection)
  }

  function handleChangeNarrativeDirection(direction: string) {
    setNarrativeDirection(direction)
    if (narrativeTarget) void showNarrativeForDirection(narrativeTarget, direction)
  }

  async function handleGenerateNarrative() {
    if (!narrativeTarget) return
    const scenarioId = narrativeTarget.id
    const direction = narrativeDirection
    setNarrativeGenerating(true)
    setNarrativeError('')
    setNarrativeNotice('')
    try {
      // Старт фоновой генерации (202), затем поллинг готовности. На CPU без GPU
      // отчёт готовится десятки минут — это штатно, окно можно закрыть.
      await startNarrative(scenarioId, direction)
      mergeNarrative(scenarioId, direction, { status: 'running' })
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } }; message?: string })
          ?.response?.data?.detail ??
        (err as { message?: string })?.message ??
        'Не удалось запустить генерацию оценки и прогноза ИИ'
      setNarrativeError(message)
      setNarrativeGenerating(false)
      return
    }
    await pollNarrative(scenarioId, direction)
  }

  // «Сгенерировать все направления»: сервер обрабатывает их ПОСЛЕДОВАТЕЛЬНО, здесь
  // опрашиваем статусы всех направлений и обновляем ✓ по мере готовности. Режим
  // fire-and-forget: окно можно закрыть, прогресс сохраняется.
  async function handleGenerateAll() {
    if (!narrativeTarget) return
    const scenarioId = narrativeTarget.id
    const codes = NARRATIVE_DIRECTIONS.map((d) => d.code)
    setNarrativeError('')
    setNarrativeBatch(true)
    try {
      await startAllNarratives(scenarioId, codes, false)
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } }; message?: string })
          ?.response?.data?.detail ??
        (err as { message?: string })?.message ??
        'Не удалось запустить генерацию всех направлений'
      setNarrativeError(message)
      setNarrativeBatch(false)
      return
    }
    try {
      for (;;) {
        let doneCount = 0
        let running: string | null = null
        for (const code of codes) {
          const st = await getNarrativeStatus(scenarioId, code)
          mergeNarrative(scenarioId, code, {
            text: st.narrative ?? undefined,
            status: (st.narrative_status as 'running' | 'done' | 'failed') ?? undefined,
            generated_at: st.narrative_generated_at ?? null,
            error: st.narrative_error ?? null,
          })
          if (st.narrative_status === 'done' || st.narrative_status === 'failed') doneCount++
          if (st.narrative_status === 'running' && !running) running = code
          // Если открыто текущее направление — синхронизируем его текст.
          if (code === narrativeDirection && st.narrative_status === 'done') {
            setNarrativeText(st.narrative ?? '')
          }
        }
        const label = running
          ? NARRATIVE_DIRECTIONS.find((d) => d.code === running)?.label ?? running
          : '—'
        setNarrativeNotice(
          `Генерация всех направлений: готово ${doneCount}/${codes.length}. ` +
            `Сейчас: ${label}. На CPU это долго — можно закрыть окно, прогресс сохраняется.`
        )
        if (doneCount >= codes.length) {
          setNarrativeNotice('')
          break
        }
        await new Promise((r) => setTimeout(r, 15000))
      }
    } catch {
      /* поллинг best-effort */
    } finally {
      setNarrativeBatch(false)
    }
  }

  async function handleRecalculate() {
    if (!paramsTarget) return
    setRecalculating(true)
    try {
      await patchScenarioParameters(paramsTarget.id, params)
      const updated = await recalculateScenario(paramsTarget.id)
      const mapped = mapApiScenario(updated)
      setScenarios((prev) => prev.map((s) => (s.id === paramsTarget.id ? mapped : s)))
      setParamsTarget(null)
    } catch {
      // Silent fail — keep modal open
    } finally {
      setRecalculating(false)
    }
  }

  function openCreateTask() {
    setTaskFormData({ title: '', category: '', deadline: '', status: 'pending', region_id: skoRegionId })
    setTaskForm({ mode: 'create' })
  }

  function openEditTask(t: AiTask) {
    setTaskFormData({
      title: t.title,
      category: t.category ?? '',
      deadline: t.deadline ? t.deadline.substring(0, 10) : '',
      status: t.status,
      region_id: t.region_id || skoRegionId,
    })
    setTaskForm({ mode: 'edit', task: t })
  }

  async function handleSaveTask() {
    if (!tasksModal || !taskForm) return
    setTaskSaving(true)
    try {
      if (taskForm.mode === 'create') {
        const body: TaskCreate = {
          scenario_id: tasksModal.id,
          region_id: taskFormData.region_id,
          title: taskFormData.title,
          category: taskFormData.category || undefined,
          deadline: taskFormData.deadline || undefined,
          status: taskFormData.status,
        }
        const created = await createTask(body)
        setTasks((prev) => [...prev, created])
      } else if (taskForm.task) {
        const body: TaskUpdate = {
          title: taskFormData.title,
          category: taskFormData.category || undefined,
          deadline: taskFormData.deadline || undefined,
          status: taskFormData.status,
          region_id: taskFormData.region_id,
        }
        const updated = await updateTask(taskForm.task.id, body)
        setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)))
      }
      setTaskForm(null)
    } catch {
      // Silent — keep form open
    } finally {
      setTaskSaving(false)
    }
  }

  async function handleDeleteTask(taskId: string) {
    if (!confirm('Удалить задачу?')) return
    await deleteTask(taskId)
    setTasks((prev) => prev.filter((t) => t.id !== taskId))
  }

  const visibleScenarios = scenarios.filter((s) => {
    if (tab === 'current') return s.status === 'approved'
    if (tab === 'history') return s.status === 'archived'
    return s.status !== 'archived'  // 'all' shows non-archived
  })

  return (
    <>
      {/* Header */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Сценарии развития</h1>
          <p className="mt-0.5 text-sm text-gray-500">
            Прогнозные сценарии для Северо-Казахстанской области
          </p>
        </div>
        {canGenerate && (() => {
          const hasActive = scenarios.some((s) => s.status === 'active' || s.status === 'draft')
          const canGen = !hasActive
          return (
            <div className="flex flex-col items-end gap-1">
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1.5 text-sm text-gray-600">
                  Целевой год
                  <select
                    value={targetYear}
                    onChange={(e) => setTargetYear(Number(e.target.value))}
                    disabled={generating || !canGen}
                    className="rounded-lg border border-gray-300 bg-white px-2 py-2 text-sm text-gray-800 focus:border-primary-500 focus:outline-none disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {Array.from({ length: 15 }, (_, i) => 2026 + i).map((y) => (
                      <option key={y} value={y}>{y}</option>
                    ))}
                  </select>
                </label>
                <button
                  onClick={handleGenerate}
                  disabled={generating || !canGen}
                  title={hasActive ? 'Завершите текущий цикл: утвердите или отклоните все активные сценарии' : ''}
                  className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {generating ? 'Генерация...' : '+ Сгенерировать сценарии'}
                </button>
              </div>
              {hasActive && (
                <p className="text-xs text-gray-400">Утвердите или отклоните текущие сценарии</p>
              )}
            </div>
          )
        })()}
      </div>

      {/* Tab bar */}
      <div className="mb-6 flex gap-1 rounded-lg border border-gray-200 bg-white p-1 w-fit shadow-sm">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-md px-4 py-1.5 text-sm font-medium transition-colors ${
              tab === t.id
                ? 'bg-primary-600 text-white'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Generate error */}
      {generateError && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {generateError}
        </div>
      )}

      {/* Scenario cards */}
      <div className="mb-8 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {loading
          ? Array.from({ length: 3 }).map((_, i) => <SkeletonCard key={i} />)
          : visibleScenarios.length === 0
            ? (
              <div className="lg:col-span-3 rounded-xl bg-white py-16 text-center shadow-sm ring-1 ring-gray-100">
                <p className="text-gray-400">Сценарии не найдены</p>
              </div>
            )
            : visibleScenarios.map((sc) => {
                const style = SCENARIO_STYLE[sc.type]
                const statusCfg = STATUS_LABEL[sc.status]
                const isApproving = approvingId === sc.id
                return (
                  <div
                    key={sc.id}
                    className={`rounded-xl border-t-4 bg-white p-5 shadow-sm ring-1 ring-gray-100 flex flex-col ${style.border} ${sc.status === 'archived' ? 'opacity-50' : ''}`}
                  >
                    <div className="mb-3 flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="text-2xl" aria-hidden="true">
                          {style.icon}
                        </span>
                        <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${style.badge}`}>
                          {style.badgeText}
                        </span>
                      </div>
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusCfg.color}`}>
                        {statusCfg.label}
                      </span>
                    </div>

                    <p className="mb-4 flex-1 text-sm text-gray-600 leading-relaxed">{sc.description}</p>

                    {sc.status === 'draft' && sc.rejectionReason && (
                      <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                        <span className="font-semibold">Причина отклонения: </span>{sc.rejectionReason}
                      </div>
                    )}

                    {sc.status === 'approved' && sc.approvedAt && (
                      <div className="mb-3 rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-800">
                        <span className="font-semibold">Утверждён: </span>
                        {sc.approvedBy}, {new Date(sc.approvedAt).toLocaleString('ru-RU')}
                      </div>
                    )}

                    {sc.status === 'approved' && sc.approvalComment && (
                      <div className="mb-3 rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-800">
                        <span className="font-semibold">Комментарий к утверждению: </span>{sc.approvalComment}
                      </div>
                    )}

                    <div className="mb-2 text-xs font-medium text-gray-500">
                      Прогноз на <span className="text-gray-800">{sc.targetYear}</span> год
                    </div>

                    <div className="mb-4 space-y-2 rounded-lg bg-gray-50 p-3">
                      {COMPARE_METRICS.map((m) => (
                        <div key={m.key} className="flex justify-between text-sm">
                          <span className="text-gray-500">{m.label}</span>
                          <span
                            className={`font-semibold ${
                              sc[m.key].startsWith('-') ? 'text-red-600' : 'text-gray-900'
                            }`}
                          >
                            {sc[m.key]}
                          </span>
                        </div>
                      ))}
                    </div>

                    <ScenarioLandscapeBlock
                      scenarioId={sc.id}
                      snapshot={sc.landscape}
                      canEdit={canLinkLandscape && sc.status !== 'approved' && sc.status !== 'archived'}
                      onChanged={loadScenarios}
                    />

                    <div className="flex items-center justify-between text-xs text-gray-400">
                      <span>{new Date(sc.createdAt).toLocaleDateString('ru-RU')}</span>
                      {sc.approvedAt && <span>&#10003; утверждён {new Date(sc.approvedAt).toLocaleDateString('ru-RU')}</span>}
                    </div>

                    {/* Action buttons */}
                    <div className="mt-4 flex flex-col gap-2">
                      {/* Tasks */}
                      <button
                        onClick={() => handleOpenTasks(sc)}
                        className="w-full rounded-lg border border-gray-200 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 transition-colors"
                      >
                        Задачи по сценарию ↓
                      </button>

                      {/* AI narrative — autonomous local LLM */}
                      <button
                        onClick={() => handleOpenNarrative(sc)}
                        className="w-full rounded-lg border border-purple-200 py-1.5 text-xs font-medium text-purple-700 hover:bg-purple-50 transition-colors"
                        title="Генерация текстового анализа сценария локальной автономной ИИ-моделью"
                      >
                        {(() => {
                          const done = Object.values(sc.narratives ?? {}).filter(
                            (e) => e?.status === 'done'
                          ).length
                          if (done > 0) return `Оценка и прогноз ИИ ✓ (${done})`
                          return sc.narrative ? 'Оценка и прогноз ИИ ✓' : 'Сгенерировать оценку и прогноз ИИ ✨'
                        })()}
                      </button>

                      {/* Parameters (exp_dir, akim_obl) */}
                      {canEditParams && sc.status !== 'approved' && (
                        <button
                          onClick={() => handleOpenParams(sc)}
                          className="w-full rounded-lg border border-primary-200 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-50 transition-colors"
                        >
                          Управляющие параметры ⚙
                        </button>
                      )}

                      {/* Approve / Reject */}
                      {canApprove && sc.status === 'active' && (
                        <div className="flex gap-2">
                          <button
                            onClick={() => setRejectTarget(sc)}
                            className="flex-1 rounded-lg border border-red-200 py-2 text-sm font-medium text-red-600 hover:bg-red-50 transition-colors"
                          >
                            Отклонить
                          </button>
                          <button
                            onClick={() => setApproveTarget(sc)}
                            disabled={isApproving}
                            className="flex-1 rounded-lg bg-primary-600 py-2 text-sm font-medium text-white hover:bg-primary-700 transition-colors disabled:opacity-60"
                          >
                            Утвердить
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
      </div>

      {/* Tasks list for "Текущий" tab */}
      {tab === 'current' && !loading && (
        <div className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 overflow-hidden">
          <div className="border-b border-gray-100 px-5 py-4">
            <h2 className="text-sm font-semibold text-gray-900">Задачи утверждённого сценария</h2>
          </div>
          {currentTasksLoading ? (
            <div className="p-6 text-center text-sm text-gray-400">Загрузка задач...</div>
          ) : currentTasks.length === 0 ? (
            <div className="p-6 text-center text-sm text-gray-400">Задачи не найдены</div>
          ) : (
            <div className="divide-y divide-gray-50">
              {currentTasks.map((t) => (
                <div key={t.id} className="flex items-center justify-between px-5 py-3 hover:bg-gray-50/50">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-800 truncate">{t.title}</p>
                    {t.category && <p className="text-xs text-gray-400">{t.category}</p>}
                  </div>
                  <div className="ml-4 flex items-center gap-3 flex-shrink-0">
                    {t.deadline && (
                      <span className="text-xs text-gray-400">
                        {new Date(t.deadline).toLocaleDateString('ru-RU')}
                      </span>
                    )}
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                      t.status === 'completed' || t.status === 'accepted' ? 'bg-green-100 text-green-700' :
                      t.status === 'in_progress' ? 'bg-blue-100 text-blue-700' :
                      t.status === 'cancelled' ? 'bg-gray-100 text-gray-500' :
                      'bg-yellow-100 text-yellow-700'
                    }`}>
                      {t.status === 'accepted' ? 'Принята' :
                       t.status === 'completed' ? 'Выполнено' :
                       t.status === 'in_progress' ? 'В работе' :
                       t.status === 'cancelled' ? 'Отменено' : 'Новая'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Compare table */}
      {tab === 'all' && !loading && visibleScenarios.length > 0 && (
        <div className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100 overflow-hidden">
          <div className="border-b border-gray-100 px-5 py-4">
            <h2 className="text-sm font-semibold text-gray-900">Сравнение сценариев</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 w-40">
                    Показатель
                  </th>
                  {visibleScenarios.map((sc) => {
                    const style = SCENARIO_STYLE[sc.type]
                    return (
                      <th
                        key={sc.id}
                        className="px-5 py-3 text-center text-xs font-medium text-gray-500"
                      >
                        <span className={`rounded-full px-2 py-0.5 ${style.badge}`}>
                          {style.badgeText}
                        </span>
                      </th>
                    )
                  })}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {COMPARE_METRICS.map((m) => {
                  const values = visibleScenarios.map((sc) => parseFloat(sc[m.key]) || 0)
                  const best = Math.max(...values)

                  return (
                    <tr key={m.key} className="hover:bg-gray-50/50">
                      <td className="px-5 py-3 font-medium text-gray-700">{m.label}</td>
                      {visibleScenarios.map((sc) => {
                        const val = parseFloat(sc[m.key]) || 0
                        const isBest = val === best
                        return (
                          <td
                            key={sc.id}
                            className={`px-5 py-3 text-center font-semibold ${
                              sc[m.key].startsWith('-')
                                ? 'text-red-600'
                                : isBest
                                  ? 'text-green-600'
                                  : 'text-gray-800'
                            }`}
                          >
                            {isBest && !sc[m.key].startsWith('-') ? '★ ' : ''}
                            {sc[m.key]}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* KPI bar charts */}
      {tab === 'all' && !loading && visibleScenarios.length > 0 && (
        <div className="mt-6 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
          <h2 className="mb-4 text-sm font-semibold text-gray-900">Диаграммы KPI</h2>
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">
            {[
              { label: 'Валовая продукция СХ, млрд ₸', key: 'vrp' as const, color: { optimal: '#22c55e', stable: '#3b82f6', risk: '#ef4444' } },
              { label: 'Население, тыс. чел.', key: 'population' as const, color: { optimal: '#22c55e', stable: '#3b82f6', risk: '#ef4444' } },
              { label: 'Инвестиции в АПК, млрд ₸', key: 'investment' as const, color: { optimal: '#22c55e', stable: '#3b82f6', risk: '#ef4444' } },
            ].map((metric) => {
              const vals = visibleScenarios.map((sc) => ({
                type: sc.type,
                label: SCENARIO_STYLE[sc.type].badgeText,
                raw: parseFloat(sc[metric.key]) || 0,
                display: sc[metric.key],
              }))
              const max = Math.max(...vals.map((v) => Math.abs(v.raw)), 1)
              return (
                <div key={metric.key}>
                  <div className="mb-2 text-xs font-medium text-gray-500">{metric.label}</div>
                  <div className="space-y-2">
                    {vals.map((v) => (
                      <div key={v.type}>
                        <div className="mb-0.5 flex justify-between text-xs text-gray-600">
                          <span>{v.label}</span>
                          <span className="font-semibold">{v.display}</span>
                        </div>
                        <div className="h-5 w-full rounded-full bg-gray-100 overflow-hidden">
                          <div
                            className="h-full rounded-full transition-all duration-500"
                            style={{
                              width: `${Math.max(4, (Math.abs(v.raw) / max) * 100)}%`,
                              backgroundColor: metric.color[v.type],
                              opacity: v.raw < 0 ? 0.6 : 1,
                            }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Approve modal */}
      {approveTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="approve-title"
        >
          <div className="w-full max-w-md rounded-xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <h3 id="approve-title" className="font-semibold text-gray-900">
                Утверждение сценария
              </h3>
              <button
                onClick={() => { setApproveTarget(null); setApproveError('') }}
                className="text-xl leading-none text-gray-400 hover:text-gray-700"
                aria-label="Закрыть"
              >
                ×
              </button>
            </div>

            <div className="p-6 space-y-4">
              <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-3 text-sm text-yellow-800">
                <strong>Внимание:</strong> Утверждение сценария{' '}
                <span className="font-semibold">
                  «{SCENARIO_STYLE[approveTarget.type].badgeText}»
                </span>{' '}
                обязывает районы к его исполнению. Действие фиксируется в журнале аудита.
              </div>

              {approveError && (
                <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  {approveError}
                </div>
              )}

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">
                  Комментарий к решению
                </label>
                <textarea
                  value={approvalComment}
                  onChange={(e) => setApprovalComment(e.target.value)}
                  rows={3}
                  placeholder="Обоснование решения об утверждении..."
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                  autoFocus
                />
              </div>
            </div>

            <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
              <button
                onClick={() => { setApproveTarget(null); setApproveError('') }}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                Отмена
              </button>
              <button
                onClick={handleApprove}
                disabled={approvingId !== null}
                className="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {approvingId ? 'Сохраняю...' : 'Утвердить'}
              </button>
            </div>
          </div>
        </div>
      )}
      {/* Reject modal */}
      {rejectTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-md rounded-xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <h3 className="font-semibold text-gray-900">Отклонение сценария</h3>
              <button onClick={() => { setRejectTarget(null); setRejectError('') }} className="text-xl leading-none text-gray-400 hover:text-gray-700">×</button>
            </div>
            <div className="p-6 space-y-4">
              {rejectError && <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{rejectError}</div>}
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Причина отклонения</label>
                <textarea
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  rows={3}
                  placeholder="Укажите что необходимо скорректировать..."
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-red-400 focus:outline-none focus:ring-1 focus:ring-red-400"
                  autoFocus
                />
              </div>
            </div>
            <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
              <button onClick={() => { setRejectTarget(null); setRejectError('') }} className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">Отмена</button>
              <button
                onClick={handleReject}
                disabled={rejecting || !rejectReason.trim()}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {rejecting ? 'Отклоняю...' : 'Подтвердить отклонение'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Tasks modal */}
      {tasksModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-lg rounded-xl bg-white shadow-2xl flex flex-col max-h-[90vh]">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4 flex-shrink-0">
              <h3 className="font-semibold text-gray-900">Задачи — {tasksModal.label}</h3>
              <div className="flex items-center gap-2">
                {canManageTasks && !taskForm && (
                  <button
                    onClick={openCreateTask}
                    className="rounded-lg bg-primary-600 px-3 py-1 text-xs font-medium text-white hover:bg-primary-700"
                  >
                    + Создать
                  </button>
                )}
                <button onClick={() => { setTasksModal(null); setTaskForm(null) }} className="text-xl leading-none text-gray-400 hover:text-gray-700">×</button>
              </div>
            </div>

            {/* Task create/edit form */}
            {taskForm && (
              <div className="border-b border-gray-100 p-4 bg-primary-50 flex-shrink-0 space-y-3">
                <p className="text-xs font-semibold text-primary-700 uppercase tracking-wide">
                  {taskForm.mode === 'create' ? 'Новая задача' : 'Редактирование'}
                </p>
                <input
                  type="text"
                  value={taskFormData.title}
                  onChange={(e) => setTaskFormData((d) => ({ ...d, title: e.target.value }))}
                  placeholder="Название задачи *"
                  className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-primary-500 focus:outline-none"
                />
                <input
                  type="text"
                  value={taskFormData.category}
                  onChange={(e) => setTaskFormData((d) => ({ ...d, category: e.target.value }))}
                  placeholder="Категория (необязательно)"
                  className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-primary-500 focus:outline-none"
                />
                <select
                  value={taskFormData.region_id}
                  onChange={(e) => setTaskFormData((d) => ({ ...d, region_id: e.target.value }))}
                  className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-primary-500 focus:outline-none"
                >
                  {regions
                    .slice()
                    .sort((a, b) => a.level - b.level || a.name.localeCompare(b.name, 'ru'))
                    .map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.level === 1 ? `🌍 ${r.name} (область)` : `📍 ${r.name}`}
                      </option>
                    ))}
                </select>
                <div className="flex gap-2">
                  <input
                    type="date"
                    value={taskFormData.deadline}
                    onChange={(e) => setTaskFormData((d) => ({ ...d, deadline: e.target.value }))}
                    className="flex-1 rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-primary-500 focus:outline-none"
                  />
                  <select
                    value={taskFormData.status}
                    onChange={(e) => setTaskFormData((d) => ({ ...d, status: e.target.value }))}
                    className="flex-1 rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-primary-500 focus:outline-none"
                  >
                    <option value="pending">Новая</option>
                    <option value="in_progress">В работе</option>
                    <option value="completed">Выполнено</option>
                    <option value="cancelled">Отменено</option>
                  </select>
                </div>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setTaskForm(null)} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50">Отмена</button>
                  <button
                    onClick={handleSaveTask}
                    disabled={taskSaving || !taskFormData.title.trim()}
                    className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-60"
                  >
                    {taskSaving ? 'Сохраняю...' : 'Сохранить'}
                  </button>
                </div>
              </div>
            )}

            <div className="overflow-y-auto p-6 space-y-3 flex-1">
              {tasksLoading ? (
                <div className="py-8 text-center text-sm text-gray-400">Загрузка...</div>
              ) : tasks.length === 0 ? (
                <div className="py-8 text-center text-sm text-gray-400">
                  Нет задач. {canManageTasks ? 'Создайте первую задачу.' : ''}
                </div>
              ) : (
                tasks.map((t) => (
                  <div key={t.id} className="rounded-lg border border-gray-100 bg-gray-50 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-800 truncate">{t.title}</p>
                        {t.category && <p className="text-xs text-gray-400">{t.category}</p>}
                        {t.region_id && (
                          <p className="text-xs text-gray-400">
                            📍 {regions.find((r) => r.id === t.region_id)?.name ?? '—'}
                          </p>
                        )}
                      </div>
                      <div className="flex items-center gap-1 flex-shrink-0">
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                          t.status === 'completed' || t.status === 'accepted' ? 'bg-green-100 text-green-700' :
                          t.status === 'in_progress' ? 'bg-blue-100 text-blue-700' :
                          t.status === 'cancelled' ? 'bg-gray-100 text-gray-500' :
                          'bg-yellow-100 text-yellow-700'
                        }`}>
                          {t.status === 'accepted' ? 'Принята' :
                       t.status === 'completed' ? 'Выполнено' :
                           t.status === 'in_progress' ? 'В работе' :
                           t.status === 'cancelled' ? 'Отменено' : 'Новая'}
                        </span>
                        {canManageTasks && (
                          <>
                            <button
                              onClick={() => openEditTask(t)}
                              className="ml-1 rounded px-1.5 py-0.5 text-xs text-primary-600 hover:bg-primary-50"
                              title="Редактировать"
                            >✎</button>
                            <button
                              onClick={() => handleDeleteTask(t.id)}
                              className="rounded px-1.5 py-0.5 text-xs text-red-400 hover:bg-red-50"
                              title="Удалить"
                            >✕</button>
                          </>
                        )}
                      </div>
                    </div>
                    {t.deadline && (
                      <p className="mt-1 text-xs text-gray-400">
                        Срок: {new Date(t.deadline).toLocaleDateString('ru-RU')}
                      </p>
                    )}
                  </div>
                ))
              )}
            </div>
            <div className="border-t border-gray-100 px-6 py-3 text-right flex-shrink-0">
              <button onClick={() => { setTasksModal(null); setTaskForm(null) }} className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">Закрыть</button>
            </div>
          </div>
        </div>
      )}

      {/* Parameters modal */}
      {paramsTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-lg rounded-xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <h3 className="font-semibold text-gray-900">Управляющие параметры — {SCENARIO_STYLE[paramsTarget.type].badgeText}</h3>
              <button onClick={() => setParamsTarget(null)} className="text-xl leading-none text-gray-400 hover:text-gray-700">×</button>
            </div>
            <div className="p-6 space-y-5">
              {[
                { key: 'investment_multiplier' as const, label: 'Мультипликатор инвестиций', min: 0.5, max: 2.0, step: 0.05, format: (v: number) => `×${v.toFixed(2)}` },
                { key: 'gdp_growth_target' as const, label: 'Целевой рост продукции СХ (%/год)', min: -0.1, max: 0.2, step: 0.01, format: (v: number) => `${(v * 100).toFixed(1)}%` },
                { key: 'infrastructure_index' as const, label: 'Индекс инфраструктуры', min: 0.5, max: 2.0, step: 0.05, format: (v: number) => `×${v.toFixed(2)}` },
                { key: 'population_growth_rate' as const, label: 'Прирост населения (%/год)', min: -0.05, max: 0.05, step: 0.005, format: (v: number) => `${(v * 100).toFixed(2)}%` },
              ].map((cfg) => {
                const val = params[cfg.key] ?? 1.0
                return (
                  <div key={cfg.key}>
                    <div className="flex justify-between mb-1">
                      <label className="text-sm font-medium text-gray-700">{cfg.label}</label>
                      <span className="text-sm font-semibold text-primary-600">{cfg.format(val)}</span>
                    </div>
                    <input
                      type="range"
                      min={cfg.min}
                      max={cfg.max}
                      step={cfg.step}
                      value={val}
                      onChange={(e) => setParams((p) => ({ ...p, [cfg.key]: parseFloat(e.target.value) }))}
                      className="w-full accent-primary-600"
                    />
                    <div className="flex justify-between text-xs text-gray-400">
                      <span>{cfg.format(cfg.min)}</span>
                      <span>{cfg.format(cfg.max)}</span>
                    </div>
                  </div>
                )
              })}
            </div>
            <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
              <button onClick={() => setParamsTarget(null)} className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">Отмена</button>
              <button
                onClick={handleRecalculate}
                disabled={recalculating}
                className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {recalculating ? 'Пересчёт...' : 'Пересчитать'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* AI Narrative drawer (autonomous local LLM) */}
      {narrativeTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-3xl max-h-[90vh] flex flex-col rounded-xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <div>
                <h3 className="text-lg font-semibold text-gray-900">
                  Оценка и прогноз ИИ: {SCENARIO_STYLE[narrativeTarget.type].badgeText}
                </h3>
                <p className="text-xs text-gray-500 mt-1">
                  Генерация локальной автономной ИИ-моделью. Никаких внешних API.
                </p>
              </div>
              <button
                onClick={() => setNarrativeTarget(null)}
                className="text-2xl leading-none text-gray-400 hover:text-gray-600"
                aria-label="Закрыть"
              >
                ×
              </button>
            </div>

            <div className="border-b border-gray-100 px-6 py-3 bg-gray-50">
              <div className="flex items-center gap-3">
                <label htmlFor="narrative-direction" className="text-sm font-medium text-gray-700 whitespace-nowrap">
                  Данные направления:
                </label>
                <select
                  id="narrative-direction"
                  value={narrativeDirection}
                  onChange={(e) => handleChangeNarrativeDirection(e.target.value)}
                  disabled={narrativeGenerating || narrativeBatch}
                  title="Показатели какого направления использовать для оценки и прогноза ИИ"
                  className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
                >
                  {NARRATIVE_DIRECTIONS.map((d) => {
                    const st = narrativeTarget.narratives?.[d.code]?.status
                    const mark = st === 'done' ? ' ✓' : st === 'running' ? ' …' : ''
                    return (
                      <option key={d.code} value={d.code}>
                        {d.label}{mark}
                      </option>
                    )
                  })}
                </select>
                <button
                  onClick={handleGenerateNarrative}
                  disabled={narrativeGenerating || narrativeBatch}
                  className="ml-auto rounded-lg bg-purple-600 px-4 py-2 text-sm font-medium text-white hover:bg-purple-700 disabled:opacity-60"
                >
                  {narrativeGenerating
                    ? 'Генерация… (можно закрыть окно)'
                    : narrativeText
                      ? 'Перегенерировать'
                      : 'Сгенерировать'}
                </button>
                <button
                  onClick={handleGenerateAll}
                  disabled={narrativeGenerating || narrativeBatch}
                  title="Последовательно сгенерировать оценку и прогноз по всем направлениям (пропуская уже готовые). Можно закрыть окно — прогресс сохранится."
                  className="rounded-lg border border-purple-300 px-3 py-2 text-sm font-medium text-purple-700 hover:bg-purple-50 disabled:opacity-60"
                >
                  {narrativeBatch ? 'Идёт по всем…' : 'Все направления'}
                </button>
              </div>
              <p className="mt-2 text-xs text-gray-500">
                Оценка и прогноз ИИ опираются на показатели выбранного направления по районам области.
                Текст каждого направления сохраняется отдельно — смена направления
                показывает его сохранённый текст (✓), а не перезаписывает соседний.
                «Все направления» ставит их в очередь на сервере (по очереди, пропуская готовые).
              </p>
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-4">
              {narrativeError && (
                <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {narrativeError}
                </div>
              )}
              {narrativeNotice && !narrativeError && (
                <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700 flex items-start gap-2">
                  <div className="mt-0.5 inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-blue-200 border-t-blue-600" />
                  <span>{narrativeNotice}</span>
                </div>
              )}
              {!narrativeText && !narrativeGenerating && !narrativeError && !narrativeNotice && (
                <div className="py-12 text-center text-gray-500">
                  <p className="text-sm">Оценка и прогноз ИИ ещё не сгенерированы.</p>
                  <p className="text-xs mt-1">
                    Выберите направление и нажмите «Сгенерировать».
                  </p>
                </div>
              )}
              {narrativeGenerating && !narrativeText && !narrativeNotice && (
                <div className="py-12 text-center text-gray-500">
                  <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-purple-200 border-t-purple-600" />
                  <p className="text-sm mt-3">
                    Локальная ИИ-модель генерирует анализ в фоне. На сервере без GPU
                    это занимает десятки минут; можно закрыть окно — текст
                    сохранится и появится по готовности.
                  </p>
                </div>
              )}
              {narrativeText && (
                <div className="prose prose-sm max-w-none whitespace-pre-wrap text-gray-800 leading-relaxed">
                  {/* Заголовок раздела зашит в сохранённые тексты — в интерфейсе слово «нарратив» не показываем. */}
                  {narrativeText.replace('Прогнозный нарратив', 'Прогноз')}
                </div>
              )}
            </div>

            {narrativeTarget.narratives?.[narrativeDirection]?.generated_at && (
              <div className="border-t border-gray-100 px-6 py-3 text-xs text-gray-500">
                Сгенерировано ({NARRATIVE_DIRECTIONS.find((d) => d.code === narrativeDirection)?.label ?? narrativeDirection}):{' '}
                {new Date(narrativeTarget.narratives[narrativeDirection]!.generated_at!).toLocaleString('ru-RU')}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
