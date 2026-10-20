import axios, { AxiosInstance } from 'axios'
import { attachRefresh } from '@/lib/api'

// ИИ-запросы идут через наш бэкенд-прокси (/api/v1/ai/*), а не напрямую на
// dssai: бэкенд сам инжектит X-AI-Key и проверяет RBAC по направлениям. Поэтому
// здесь нет секретного ключа — только сеанс DSS в httpOnly-cookie.
//
// База — тот же абсолютный backend, что и у основного клиента
// (NEXT_PUBLIC_API_URL = https://dssapi.dwh.kz/api/v1), плюс префикс /ai.
// Относительный '/api/v1/ai' нельзя: фронт на dss.dwh.kz, а /api/v1/* туда не
// проксируется (основной клиент тоже ходит на абсолютный dssapi).
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? '/api/v1'
const aiApi: AxiosInstance = axios.create({
  baseURL: `${API_BASE}/ai`,
  headers: {
    'Content-Type': 'application/json',
  },
  // Сеанс — в httpOnly-cookie API (H-1).
  withCredentials: true,
})

// Автообновление токена на 401, как у основного клиента: нарратив поллит статус
// несколько минут, за это время access-токен может истечь.
attachRefresh(aiApi)

export interface ScenarioKPIs {
  gdp_growth: number
  employment_rate: number
  investment_volume: number
  population_income?: number
  industrial_output?: number
}

export interface Scenario {
  id: string
  scenario_type: 'optimal' | 'stable' | 'risk'
  status: string
  parameters: Record<string, unknown> | null
  target_year: number
  region_id: string
  created_at: string
  approved_at?: string | null
  narrative?: string | null
  narrative_generated_at?: string | null
  // Нарративы по направлениям: {direction_code: {text, status, generated_at, error}}.
  narratives?: Record<string, NarrativeEntry> | null
}

export interface NarrativeEntry {
  text?: string | null
  status?: 'running' | 'done' | 'failed' | null
  generated_at?: string | null
  error?: string | null
}

export interface NarrativeResponse {
  scenario_id: string
  narrative: string
  generated_at: string
  model: string
  elapsed_seconds: number
}

export interface GeoContextAggregate {
  region_id: string
  region_name: string
  indicator_id: string
  indicator_name: string
  unit: string | null
  avg: number | null
  min: number | null
  max: number | null
  n: number
  latest_period: string | null
}

export interface GeoContext {
  direction: { code: string; name: string }
  region: { id: string; code: string; name: string; level: number }
  child_regions_geojson: { type: 'FeatureCollection'; features: unknown[] }
  aggregates: GeoContextAggregate[]
  meta: {
    simplify_tolerance: number
    child_count: number
    geojson_feature_count: number
    indicator_aggregate_count: number
  }
}

export interface AiTask {
  id: string
  title: string
  description: string | null
  category: string | null
  target_metric: string | null
  target_value: number | null
  unit: string | null
  scenario_id: string
  scenario_type: string | null
  target_year: number | null
  region_id: string
  status: 'pending' | 'in_progress' | 'completed' | 'cancelled' | 'accepted'
  assigned_to: string | null
  deadline: string | null
  completion_report: string | null
  created_at: string
  updated_at: string
}

export interface TaskCreate {
  scenario_id: string
  region_id: string
  title: string
  description?: string
  category?: string
  target_metric?: string
  target_value?: number
  unit?: string
  deadline?: string
  status?: string
  assigned_to?: string
}

export interface TaskUpdate {
  title?: string
  description?: string
  category?: string
  target_metric?: string
  target_value?: number
  unit?: string
  deadline?: string
  status?: string
  completion_report?: string
  assigned_to?: string | null
  region_id?: string
}

interface TasksParams {
  scenario_id?: string
  region_id?: string
  status?: string
}

export async function fetchScenarios(): Promise<Scenario[]> {
  try {
    const { data } = await aiApi.get<Scenario[]>('/api/v1/scenarios')
    return data
  } catch {
    return []
  }
}

export async function generateScenarios(
  region_id: string,
  target_year: number,
  created_by: string // UUID of the user
): Promise<Scenario[]> {
  const { data } = await aiApi.post<Scenario[]>('/api/v1/scenarios/generate', {
    region_id,
    target_year,
    created_by,
  })
  return data
}

export async function approveScenario(
  scenario_id: string,
  approved_by: string,
  comment: string
): Promise<void> {
  await aiApi.post('/api/v1/internal/scenario-approve', {
    scenario_id,
    approved_by,
    comment,
  })
}

// Нарратив генерируется асинхронно: старт возвращает статус running (202),
// готовность опрашивается getNarrativeStatus. На CPU генерация до ~6 мин.
export interface NarrativeStatus {
  scenario_id: string
  direction_code?: string | null
  narrative_status: 'idle' | 'running' | 'done' | 'failed'
  narrative?: string | null
  narrative_generated_at?: string | null
  narrative_error?: string | null
  model?: string | null
}

export async function startNarrative(
  scenario_id: string,
  direction_code: string
): Promise<NarrativeStatus> {
  const { data } = await aiApi.post<NarrativeStatus>(
    `/api/v1/scenarios/${scenario_id}/narrative`,
    { direction_code }
  )
  return data
}

// Краткий вывод ИИ по вкладке дашборда направления. Сервис отдаёт сохранённый
// текст; генерация (в фоне) запускается только при изменении данных вкладки —
// тогда статус running, готовность опрашивается повторным вызовом.
export interface DashboardSummary {
  page_key: string
  status: 'done' | 'running' | 'empty'
  text: string | null
  source: 'llm' | 'template' | null
  generated_at: string | null
}

export async function fetchDashboardSummary(params: {
  page_key: string
  title: string
  direction_code: string
  indicator_ids: string[]
}): Promise<DashboardSummary> {
  const { data } = await aiApi.post<DashboardSummary>('/api/v1/dashboard-summaries', params)
  return data
}

// Последовательная генерация нарративов сразу по нескольким направлениям.
// Возвращает 202 немедленно; прогресс каждого направления опрашивается через
// getNarrativeStatus. force=true — перегенерировать уже готовые.
export async function startAllNarratives(
  scenario_id: string,
  directions: string[],
  force = false
): Promise<{ scenario_id: string; queued: string[]; force: boolean }> {
  const { data } = await aiApi.post(
    `/api/v1/scenarios/${scenario_id}/narrative/all`,
    { directions, force }
  )
  return data
}

export async function getNarrativeStatus(
  scenario_id: string,
  direction_code?: string
): Promise<NarrativeStatus> {
  const { data } = await aiApi.get<NarrativeStatus>(
    `/api/v1/scenarios/${scenario_id}/narrative/status`,
    direction_code ? { params: { direction_code } } : undefined
  )
  return data
}

export async function fetchGeoContext(
  direction_code: string,
  region_id: string
): Promise<GeoContext> {
  const { data } = await aiApi.get<GeoContext>(
    `/api/v1/geo-context/${direction_code}/${region_id}`
  )
  return data
}

export async function fetchTasks(params?: TasksParams): Promise<AiTask[]> {
  try {
    const query: Record<string, string> = {}
    if (params?.scenario_id) query.scenario_id = params.scenario_id
    if (params?.region_id) query.region_id = params.region_id
    if (params?.status) query.status = params.status

    const { data } = await aiApi.get<AiTask[] | { items: AiTask[]; total: number }>('/api/v1/tasks', { params: query })
    return Array.isArray(data) ? data : data.items
  } catch {
    return []
  }
}

// Рабочие задачи районов — только сценариев, которые утверждались (approved_at).
// Задачи неутверждённых сценариев — черновики предпросмотра для эксперта
// («Задачи по сценарию»), в «Мероприятиях» и сводках им не место.
export function onlyWorkingTasks(tasks: AiTask[], scenarios: Scenario[]): AiTask[] {
  const approved = new Set(scenarios.filter((s) => s.approved_at).map((s) => s.id))
  return tasks.filter((t) => approved.has(t.scenario_id))
}

// Как fetchTasks, но различает «задач нет» ([]) и «ИИ-сервис недоступен» (null):
// обзорным экранам нельзя показывать ноль задач, когда список просто не загрузился.
export async function fetchTasksOrNull(params?: TasksParams): Promise<AiTask[] | null> {
  try {
    const query: Record<string, string> = {}
    if (params?.scenario_id) query.scenario_id = params.scenario_id
    if (params?.region_id) query.region_id = params.region_id
    if (params?.status) query.status = params.status
    const { data } = await aiApi.get<AiTask[] | { items: AiTask[]; total: number }>('/api/v1/tasks', { params: query })
    return Array.isArray(data) ? data : data.items
  } catch {
    return null
  }
}

export async function updateTaskStatus(task_id: string, status: string, completion_report?: string): Promise<AiTask> {
  const { data } = await aiApi.put<AiTask>(`/api/v1/tasks/${task_id}/status`, { status, completion_report })
  return data
}

export async function createTask(body: TaskCreate): Promise<AiTask> {
  const { data } = await aiApi.post<AiTask>('/api/v1/tasks', body)
  return data
}

export async function updateTask(task_id: string, body: TaskUpdate): Promise<AiTask> {
  const { data } = await aiApi.patch<AiTask>(`/api/v1/tasks/${task_id}`, body)
  return data
}

export async function deleteTask(task_id: string): Promise<void> {
  await aiApi.delete(`/api/v1/tasks/${task_id}`)
}

export interface TaskEvent {
  id: string
  task_id: string
  event_type: 'comment' | 'status_change' | 'accept' | 'reject_completion' | string
  old_status: string | null
  new_status: string | null
  comment: string | null
  author_id: string | null
  author_name: string | null
  created_at: string
}

export async function fetchTaskEvents(task_id: string): Promise<TaskEvent[]> {
  try {
    const { data } = await aiApi.get<TaskEvent[]>(`/api/v1/tasks/${task_id}/events`)
    return data
  } catch {
    return []
  }
}

export async function addTaskComment(
  task_id: string,
  comment: string,
  author_id?: string,
  author_name?: string
): Promise<TaskEvent> {
  const { data } = await aiApi.post<TaskEvent>(`/api/v1/tasks/${task_id}/comment`, {
    comment,
    author_id,
    author_name,
  })
  return data
}

export async function acceptTask(
  task_id: string,
  comment?: string,
  author_id?: string,
  author_name?: string
): Promise<AiTask> {
  const { data } = await aiApi.post<AiTask>(`/api/v1/tasks/${task_id}/accept`, {
    comment,
    author_id,
    author_name,
  })
  return data
}

export async function rejectTaskCompletion(
  task_id: string,
  comment: string,
  author_id?: string,
  author_name?: string
): Promise<AiTask> {
  const { data } = await aiApi.post<AiTask>(`/api/v1/tasks/${task_id}/reject-completion`, {
    comment,
    author_id,
    author_name,
  })
  return data
}

export interface ScenarioParameters {
  investment_multiplier?: number
  population_growth_rate?: number
  infrastructure_index?: number
  employment_target?: number
  gdp_growth_target?: number
}

export async function patchScenarioParameters(scenario_id: string, params: ScenarioParameters): Promise<Scenario> {
  const { data } = await aiApi.patch<Scenario>(`/api/v1/scenarios/${scenario_id}/parameters`, params)
  return data
}

export async function recalculateScenario(scenario_id: string): Promise<Scenario> {
  const { data } = await aiApi.post<Scenario>(`/api/v1/scenarios/${scenario_id}/calculate`)
  return data
}

export async function rejectScenario(scenario_id: string, reason: string, rejected_by: string): Promise<Scenario> {
  const { data } = await aiApi.post<Scenario>(`/api/v1/scenarios/${scenario_id}/reject`, {
    reason,
    rejected_by,
  })
  return data
}

// ───────────────────────── Алгоритм ЗБ-УР ─────────────────────────
// Оценка влияния земельного баланса на устойчивое развитие. Полное описание —
// docs/Алгоритм_ЗБ_УР_2026-09-09.md.

/** Категории земель — порядок совпадает с матрицей переходов на бэкенде. */
export const LAND_CATEGORIES = [
  'agricultural',
  'settlements',
  'industry',
  'protected',
  'forest',
  'water',
  'reserve',
] as const

export type LandCategory = (typeof LAND_CATEGORIES)[number]

export const LAND_CATEGORY_LABELS: Record<LandCategory, string> = {
  agricultural: 'Земли сельскохозяйственного назначения',
  settlements: 'Земли населённых пунктов',
  industry: 'Земли промышленности и транспорта',
  protected: 'Земли ООПТ',
  forest: 'Земли лесного фонда',
  water: 'Земли водного фонда',
  reserve: 'Земли запаса',
}

/** Метка типа связи, обязательная рядом с каждым числом. */
export type LinkLabel = 'Б' | 'С' | 'Э'

export interface Band {
  base: number
  low: number
  high: number
  unit: string
  link: string
}

export interface ExpressEstimate {
  scenario: Record<string, unknown>
  versions: Record<string, string>
  land_balance_delta: Record<string, number>
  agri_output: Band
  agri_output_rel: Band
  grp: Band
  grp_rel: Band
  transition_violations: Array<{ rule: string; severity: string; message: string }>
  land_use_shift_check?: LandUseShiftCheck | null
  district_check?: LandscapeDistrictCheck | null
  disclaimer: string
  unverified_assumptions: string[]
}

/** Сдвиг пашни района против размаха его посевной площади × 1,5 (гейт G3). */
export interface LandscapeDistrictCheck {
  variant_code: string
  scale: number
  districts: Array<{ region: string; arable: number; span: number; limit: number; inside: boolean }>
  outside: Array<{ region: string; arable: number; span: number; limit: number }>
  inside_count: number
  total: number
}

/** Сдвиг угодий внутри земель с/х назначения, тыс. га при полной реализации. */
export interface LandUseShift {
  arable: number
  pasture: number
}

export interface LandUseShiftCheck {
  full: Partial<LandUseShift>
  applied: Partial<LandUseShift>
  realized_fraction: number
  arable_share?: number
  historical_max_share?: number
  exceeds_history?: boolean
  note?: string
}

export interface VariantPresetComponents {
  fields_to_regulated: number
  fields_to_pasture: number
  other_to_arable: number
  other_to_irrigated: number
  fields_unzoned: number
  other_unzoned: number
}

export interface VariantPreset extends LandUseShift {
  components: VariantPresetComponents
  districts: Record<string, LandUseShift & { components: VariantPresetComponents }>
}

export interface LandscapeVariantPresets {
  version: string
  source: string
  rule: string
  variants: Record<string, VariantPreset>
}

export async function fetchLandscapeVariantPresets(): Promise<LandscapeVariantPresets> {
  const { data } = await aiApi.get<LandscapeVariantPresets>('/api/v1/landscape-impact/variant-presets')
  return data
}

export interface DistributionSummary {
  median: number
  mean: number
  ci80: [number, number]
  ci95: [number, number]
  p_positive: number
  width80: number
  n_draws: number
}

export interface GateResult {
  code: string
  title: string
  blocking: boolean
  passed: boolean
  criterion: string
  detail: string
}

export interface AssumptionItem {
  key: string
  label: string
  kind: string
  value: unknown
  unit: string
  source: string
  status: string
  verified: boolean
}

export interface LandscapeRunResult {
  scenario: Record<string, unknown>
  versions: Record<string, string>
  calibration: Record<string, any>
  transition: {
    categories: string[]
    matrix: number[][]
    net_change: Record<string, number>
    total_moved: number
    violations: Array<{ rule: string; severity: string; message: string }>
  }
  land_balance: {
    base: Record<string, number>
    scenario: Record<string, number>
    sources_of_growth: Record<string, Record<string, number>>
  }
  economy: Record<string, DistributionSummary>
  sd_index: {
    model: { mode: string; note: string; reconstruction: any[] }
    deltas: Record<string, DistributionSummary>
    mde: number
    mde_sigma: number
    official_mde: Record<string, number> | null
    index_roles: { dss: string; official: string }
  }
  sensitivity: Record<string, number>
  gates: { status: string; gates: GateResult[]; failed_blocking: string[]; failed_warning: string[] }
  verdict: {
    code: 'supports' | 'hinders' | 'inconclusive'
    label: string
    reason: string
    eco_veto: boolean
    statement: string
    details: Record<string, unknown>
  }
  trace: Array<{ quantity: string; chain: string[]; labels: LinkLabel[]; note: string }>
  assumptions: AssumptionItem[]
  issues: Array<Record<string, unknown>>
  created_at: string
}

export interface LandscapeRun {
  id: string
  name: string
  variant_code: string | null
  base_year: number
  horizon_years: number
  status: 'pending' | 'running' | 'completed' | 'failed'
  gate_status: string | null
  verdict: 'supports' | 'hinders' | 'inconclusive' | null
  algorithm_version: string
  created_at: string
  completed_at: string | null
  scenario_input?: Record<string, unknown>
  result?: LandscapeRunResult | null
  error?: string | null
  assumptions_version?: string
  datamart_version?: string
  random_seed?: number
  draws?: number
}

export interface LandscapeScenarioRequest {
  name: string
  variant_code?: string | null
  target_areas?: Record<string, number>
  land_use_shift?: LandUseShift
  horizon_years: number
  base_year?: number
  suitability_shift?: number
  draws?: number
}

export interface LandscapeCalibration {
  window: [number, number]
  balance_ok: boolean
  structural_shares: Record<string, Record<string, number>>
  yield_link: Record<string, any> | null
  yield_candidates: Array<Record<string, any>>
  direct_land_links: { n_tests: number; survived_fdr: number; min_p: number; conclusion: string }
  sd_aggregation: Record<string, any>
  mde: Record<string, number>
  vds_share_observed: Record<string, number>
  notes: string[]
  versions: Record<string, string>
  data_issues: Array<Record<string, any>>
}

export async function fetchLandscapeCalibration(): Promise<LandscapeCalibration> {
  const { data } = await aiApi.get<LandscapeCalibration>('/api/v1/landscape-impact/calibration')
  return data
}

export async function estimateLandscapeImpact(
  body: LandscapeScenarioRequest,
): Promise<ExpressEstimate> {
  const { data } = await aiApi.post<ExpressEstimate>('/api/v1/landscape-impact/estimate', body)
  return data
}

export async function createLandscapeRun(body: LandscapeScenarioRequest): Promise<LandscapeRun> {
  const { data } = await aiApi.post<LandscapeRun>('/api/v1/landscape-impact/runs', body)
  return data
}

export async function fetchLandscapeRuns(limit = 50): Promise<LandscapeRun[]> {
  const { data } = await aiApi.get<LandscapeRun[]>('/api/v1/landscape-impact/runs', {
    params: { limit },
  })
  return data
}

export async function fetchLandscapeRun(runId: string): Promise<LandscapeRun> {
  const { data } = await aiApi.get<LandscapeRun>(`/api/v1/landscape-impact/runs/${runId}`)
  return data
}

export async function compareLandscapeRuns(ids: string[]): Promise<{
  pairs: Array<Record<string, any>>
  note: string
}> {
  const { data } = await aiApi.get('/api/v1/landscape-impact/compare', {
    params: { ids: ids.join(',') },
  })
  return data
}

export interface LandscapeAssumptions {
  version: string
  algorithm_version: string
  dss_index_version: string
  assumptions: AssumptionItem[]
  unverified: string[]
  dss_index_definition: Record<string, any>
}

export async function fetchLandscapeAssumptions(): Promise<LandscapeAssumptions> {
  const { data } = await aiApi.get<LandscapeAssumptions>('/api/v1/landscape-impact/assumptions')
  return data
}

// ───────────────────────── Прогнозные модели и прогноз ─────────────────────────

export interface AiModel {
  id: string
  name: string
  version: string
  model_type: string
  indicator_id: string | null
  parameters: Record<string, unknown> | null
  metrics: Record<string, unknown> | null
  status: string
  trained_at: string | null
}

export interface ForecastInterval {
  low: number
  high: number
  level?: number
  method: 'split-conformal' | 'fixed-margin' | string
}

export interface AiForecast {
  id: string
  model_id: string
  indicator_id: string | null
  result_values: Record<string, number> | null
  confidence_scores: Record<string, ForecastInterval> | null
  xai_explanation: {
    feature_importance?: Record<string, number>
    model_confidence?: number | null
    interpretation?: string
    meta?: Record<string, unknown>
  } | null
  status: string
}

// Рабочие модели: после переобучения по одной активной на показатель.
export async function fetchActiveModels(): Promise<AiModel[]> {
  try {
    const { data } = await aiApi.get<AiModel[]>('/api/v1/models', { params: { status: 'active' } })
    return data
  } catch {
    return []
  }
}

// Прогноз по областному ряду показателя (region_id не задан — модели областные).
export async function createForecast(body: {
  model_id: string
  indicator_id: string
  horizon_years: number
}): Promise<AiForecast> {
  const { data } = await aiApi.post<AiForecast>('/api/v1/forecasts', body)
  return data
}
