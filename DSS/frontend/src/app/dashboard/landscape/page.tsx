'use client'

/**
 * Ландшафтное планирование: итоговые варианты функционального зонирования
 * V1/V2/V3 и 13 фиксированных входных растров расчёта.
 *
 * ТЗ «Требования к странице Ландшафтное планирование DSS» (28.09.2026). Расчёт
 * целиком выполняет GIS-сервис; страница только:
 *   • показывает на карте один слой за раз (вариант, вход или промежуточный слой);
 *   • заменяет вход через закреплённый за строкой slot_code (admin/data_uploader)
 *     с обязательным периодом и проверкой, что новая версия стала latest;
 *   • запускает серверный перерасчёт и показывает его статус (4 состояния,
 *     прогресс по узлам графа, skipped = переиспользован);
 *   • берёт результаты из /landscape/results, а не из общего каталога datasets;
 *   • стиль и легенда — из SLD: sld_url результата или /datasets/{id}/sld входа.
 * Доработка по документу «Доработка … SLD, замена, перерасчёт» (29.09.2026).
 * Формулы расчёта на фронт не переносятся. Оценка социально-экономического
 * эффекта (ЗБ-УР) — на подстранице /dashboard/landscape/impact.
 */

import Link from 'next/link'
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { useAuthStore } from '@/store/authStore'
import LandscapeRasterMap, { type LandscapeMapLayer } from '@/components/landscape/LandscapeRasterMap'
import {
  DEFAULT_VARIANT,
  LANDSCAPE_BLOCKS,
  LANDSCAPE_INTERMEDIATE,
  LANDSCAPE_NODES,
  LANDSCAPE_SLOTS,
  LANDSCAPE_VARIANTS,
  groupLabel,
  rampForCode,
  resultLabel,
  slotDef,
  type LandscapeBlock,
} from '@/lib/landscape/registry'
import {
  RUN_PHASE_LABEL,
  apiErrorMessage,
  datasetSldUrl,
  downloadLandscapeRaster,
  fetchLandscapeDatasetStatus,
  fetchLandscapeResults,
  fetchLandscapeRun,
  fetchLandscapeRuns,
  fetchLandscapeSlots,
  isRunActive,
  nodeStatusLabel,
  replaceLandscapeSlot,
  runPhase,
  runProgress,
  startLandscapeRun,
  waitRasterIngest,
  type LandscapeResult,
  type LandscapeRun,
  type LandscapeSlot,
  type RunPhase,
} from '@/lib/api/landscape'

type Tab = 'results' | 'inputs' | 'history'

const ACTIVE_RUN_KEY = 'dss.landscape.activeRun'

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('ru-RU')
}

function fmtPeriod(start: string | null | undefined, end: string | null | undefined): string | null {
  if (!start && !end) return null
  return `${start ? fmtDate(start) : '…'} – ${end ? fmtDate(end) : '…'}`
}

function fmtDuration(run: LandscapeRun): string {
  if (!run.started_at || !run.finished_at) return '—'
  const sec = Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000)
  if (!Number.isFinite(sec) || sec < 0) return '—'
  return sec < 60 ? `${sec} с` : `${Math.floor(sec / 60)} мин ${sec % 60} с`
}

function readStoredRun(): string | null {
  try {
    return localStorage.getItem(ACTIVE_RUN_KEY)
  } catch {
    return null
  }
}

function storeRun(id: string | null) {
  try {
    if (id) localStorage.setItem(ACTIVE_RUN_KEY, id)
    else localStorage.removeItem(ACTIVE_RUN_KEY)
  } catch {
    /* хранилище недоступно — статус просто не переживёт перезагрузку */
  }
}

const PHASE_BADGE: Record<RunPhase, string> = {
  started: 'bg-sky-100 text-sky-800',
  running: 'bg-amber-100 text-amber-800',
  done: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-rose-100 text-rose-800',
  unknown: 'bg-gray-100 text-gray-700',
}

function RunBadge({ status }: { status: string }) {
  const ph = runPhase(status)
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${PHASE_BADGE[ph]}`}>
      {ph === 'unknown' ? status : RUN_PHASE_LABEL[ph]}
    </span>
  )
}

function DatasetStatusIcon({ status }: { status: string | undefined }) {
  if (status === 'ready') {
    return <span title="Готов" className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-[11px] text-white">✓</span>
  }
  if (status === 'failed') {
    return <span title="Ошибка обработки" className="flex h-5 w-5 items-center justify-center rounded-full bg-rose-500 text-[11px] text-white">!</span>
  }
  return (
    <span
      title={status ? `Статус: ${status}` : 'Нет данных'}
      className="flex h-5 w-5 items-center justify-center rounded-full bg-amber-400 text-[11px] text-white"
    >
      …
    </span>
  )
}

function ErrorBar({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
      <span>{message}</span>
      {onRetry && (
        <button onClick={onRetry} className="rounded-md border border-rose-300 bg-white px-3 py-1 text-xs font-medium hover:bg-rose-100">
          Повторить
        </button>
      )}
    </div>
  )
}

// ── Отслеживание загруженной версии входа ────────────────────────────────────
//
// Замена создаёт новый датасет в группе слота; какой из них текущий вход, решает
// сервер (по end_date, при равенстве — по created_at). Поэтому после ready
// проверяем, что slot.latest.id совпал с загруженным id, и до этого не даём
// запускать перерасчёт («Доработка … SLD, замена, перерасчёт», §2.1).

type UploadPhase = 'processing' | 'confirmed' | 'not-latest' | 'failed'

interface TrackedUpload {
  code: string
  datasetId: string
  phase: UploadPhase
  startDate: string
  endDate: string
  error?: string | null
}

const UPLOADS_KEY = 'dss.landscape.uploads'

function readStoredUploads(): Record<string, TrackedUpload> {
  try {
    return JSON.parse(localStorage.getItem(UPLOADS_KEY) ?? '{}') as Record<string, TrackedUpload>
  } catch {
    return {}
  }
}

function storeUploads(u: Record<string, TrackedUpload>) {
  try {
    localStorage.setItem(UPLOADS_KEY, JSON.stringify(u))
  } catch {
    /* хранилище недоступно — проверка просто не переживёт перезагрузку */
  }
}

/**
 * Техническая расшифровка запуска: статус узлов и фактические dataset_id на
 * входах. Для приёмки замены: новый dataset_id входа должен появиться у узлов
 * Z → 00/02, U → 01/03, P → 20/21/30.
 */
function RunNodes({ run, currentInputs }: { run: LandscapeRun; currentInputs: Map<string, string> }) {
  if (!run.nodes || run.nodes.length === 0) {
    return <p className="text-xs text-gray-500">Сервер не вернул узлы для этого запуска.</p>
  }
  return (
    <table className="min-w-full text-xs">
      <thead className="text-left text-gray-500">
        <tr>
          <th className="py-1 pr-3 font-medium">Узел</th>
          <th className="py-1 pr-3 font-medium">Статус</th>
          <th className="py-1 font-medium">Входы (dataset_id)</th>
        </tr>
      </thead>
      <tbody className="align-top">
        {[...run.nodes]
          .sort((a, b) => a.node_code.localeCompare(b.node_code))
          .map((n) => (
            <tr key={n.node_code} className="border-t border-gray-100">
              <td className="py-1 pr-3 text-gray-900">
                {n.node_code} · {LANDSCAPE_NODES[n.node_code] ?? '—'}
              </td>
              <td className={`py-1 pr-3 ${n.status === 'failed' ? 'text-rose-700' : 'text-gray-700'}`}>
                {nodeStatusLabel(n.status)}
                {n.error && <div className="text-rose-700">{n.error}</div>}
              </td>
              <td className="py-1 text-gray-700">
                {(n.inputs ?? []).map((i) => {
                  const cur = currentInputs.get(i.group)
                  return (
                    <div key={i.group + i.dataset_id}>
                      {groupLabel(i.group)}: <span className="font-mono">{i.dataset_id.slice(0, 8)}</span>
                      {cur && (
                        <span className={cur === i.dataset_id ? 'ml-1 text-emerald-700' : 'ml-1 text-amber-700'}>
                          {cur === i.dataset_id ? '— текущий вход' : '— не текущий вход'}
                        </span>
                      )}
                    </div>
                  )
                })}
              </td>
            </tr>
          ))}
      </tbody>
    </table>
  )
}

function UploadNote({ u, onDismiss }: { u: TrackedUpload; onDismiss: () => void }) {
  const period = fmtPeriod(u.startDate, u.endDate)
  if (u.phase === 'processing') {
    return <div className="mt-0.5 text-[11px] text-amber-700">Новая версия ({period}) обрабатывается сервером…</div>
  }
  if (u.phase === 'confirmed') {
    return (
      <div className="mt-0.5 text-[11px] text-emerald-700">
        Новая версия ({period}) стала текущим входом — нужен перерасчёт
      </div>
    )
  }
  return (
    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-rose-700">
      <span>
        {u.phase === 'not-latest'
          ? `Загруженная версия (${period}) не стала текущим входом — проверьте период`
          : `Новая версия не обработана: ${u.error || 'ошибка обработки'}`}
      </span>
      <button
        onClick={(e) => {
          e.stopPropagation()
          onDismiss()
        }}
        className="underline"
      >
        {u.phase === 'not-latest' ? 'Оставить текущий вход' : 'Скрыть'}
      </button>
    </div>
  )
}

// ── Окно «Заменить входной слой» ─────────────────────────────────────────────

function ReplaceSlotModal({
  slot,
  tracked,
  onUploaded,
  onClose,
  onBusyChange,
}: {
  slot: LandscapeSlot | undefined
  tracked: TrackedUpload | undefined
  onUploaded: (u: TrackedUpload) => void
  onClose: () => void
  onBusyChange: (busy: boolean) => void
}) {
  const def = slot ? slotDef(slot.code) : undefined
  const [file, setFile] = useState<File | null>(null)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadedId, setUploadedId] = useState<string | null>(null)
  const [pct, setPct] = useState(0)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => onBusyChange(uploading), [uploading, onBusyChange])
  useEffect(() => () => onBusyChange(false), [onBusyChange])

  // Статус новой версии ведёт страница: окно можно закрыть, проверка продолжится.
  const own = tracked && tracked.datasetId === uploadedId ? tracked : undefined
  const showForm = !uploading && !own

  const submit = async () => {
    if (!slot || !file) return
    if (!/\.tiff?$/i.test(file.name)) {
      setError('Нужен файл GeoTIFF (.tif или .tiff)')
      return
    }
    if (!startDate || !endDate) {
      setError('Укажите период данных: по дате окончания сервер выбирает актуальную версию слоя')
      return
    }
    if (startDate > endDate) {
      setError('Дата начала позже даты окончания')
      return
    }
    setError(null)
    setUploading(true)
    setPct(0)
    try {
      const ds = await replaceLandscapeSlot(slot.code, file, { startDate, endDate }, setPct)
      setUploadedId(ds.id)
      onUploaded({ code: slot.code, datasetId: ds.id, phase: 'processing', startDate, endDate })
    } catch (e) {
      setError(apiErrorMessage(e, 'Не удалось загрузить файл'))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-white p-5 shadow-xl">
        <div className="mb-4 flex items-start justify-between">
          <h2 className="text-base font-semibold text-gray-900">Замена входного слоя</h2>
          <button onClick={onClose} disabled={uploading} className="text-gray-400 hover:text-gray-600 disabled:opacity-40" aria-label="Закрыть">
            ✕
          </button>
        </div>

        <dl className="mb-4 grid grid-cols-[110px_1fr] gap-y-1.5 text-sm">
          <dt className="text-gray-500">Слой</dt>
          <dd className="text-gray-900">{def?.label ?? slot?.code}</dd>
          <dt className="text-gray-500">Текущий файл</dt>
          <dd className="text-gray-700">
            {slot?.latest?.name ?? '—'}
            {slot?.latest && (
              <span className="text-gray-400">
                {' '}
                ({fmtPeriod(slot.latest.start_date, slot.latest.end_date) ?? 'период не указан'}, обновлён{' '}
                {fmtDate(slot.latest.updated_at)})
              </span>
            )}
          </dd>
        </dl>

        {showForm && (
          <div className="space-y-4">
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-gray-800">Новый файл (GeoTIFF)</span>
              <input
                type="file"
                accept=".tif,.tiff,image/tiff"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="block w-full text-sm text-gray-700 file:mr-3 file:rounded-md file:border-0 file:bg-gray-100 file:px-3 file:py-1.5 file:text-sm file:font-medium hover:file:bg-gray-200"
              />
            </label>
            <div>
              <span className="mb-1 block text-sm font-medium text-gray-800">Период данных новой версии</span>
              <div className="grid grid-cols-2 gap-3">
                <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="rounded-md border border-gray-300 px-2 py-1.5 text-sm" aria-label="Дата начала" required />
                <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="rounded-md border border-gray-300 px-2 py-1.5 text-sm" aria-label="Дата окончания" required />
              </div>
              <p className="mt-1 text-xs text-gray-500">
                Текущим входом становится версия с самой поздней датой окончания. Старая версия
                сохраняется в ГИС.
              </p>
            </div>
          </div>
        )}

        {!showForm && (
          <div className="space-y-2 rounded-lg bg-gray-50 p-3 text-sm">
            {uploading && (
              <>
                <div className="text-gray-700">Загрузка файла… {pct}%</div>
                <div className="h-1.5 overflow-hidden rounded bg-gray-200">
                  <div className="h-full bg-primary-600 transition-all" style={{ width: `${pct}%` }} />
                </div>
              </>
            )}
            {own?.phase === 'processing' && (
              <div className="text-gray-700">
                Файл загружен, сервер обрабатывает растр. Окно можно закрыть — проверка продолжится,
                а статус появится в строке слоя.
              </div>
            )}
            {own?.phase === 'confirmed' && (
              <div className="text-emerald-700">
                Новая версия готова и стала текущим входом. Чтобы обновить варианты зонирования,
                запустите перерасчёт.
              </div>
            )}
            {own?.phase === 'not-latest' && (
              <div className="text-rose-800">
                Файл обработан, но сервер не выбрал его текущим входом — вероятно, у действующей
                версии дата окончания позже. Перерасчёт заблокирован: загрузите файл с корректным
                периодом или оставьте текущий вход.
              </div>
            )}
            {own?.phase === 'failed' && (
              <div className="text-rose-800">Сервер не смог обработать растр: {own.error || 'ошибка обработки'}</div>
            )}
          </div>
        )}

        {error && <div className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</div>}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} disabled={uploading} className="rounded-md border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40">
            {showForm ? 'Отмена' : 'Закрыть'}
          </button>
          {showForm && (
            <button
              onClick={submit}
              disabled={!file || !startDate || !endDate}
              className="rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-40"
            >
              Загрузить и заменить
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Страница ─────────────────────────────────────────────────────────────────

export default function LandscapePlanningPage() {
  const { hasRole } = useAuthStore()
  const canEdit = hasRole('admin') || hasRole('data_uploader')

  const [tab, setTab] = useState<Tab>('results')
  const [slots, setSlots] = useState<LandscapeSlot[] | null>(null)
  const [results, setResults] = useState<LandscapeResult[] | null>(null)
  const [runs, setRuns] = useState<LandscapeRun[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [selectedResult, setSelectedResult] = useState(DEFAULT_VARIANT)
  const [selectedSlot, setSelectedSlot] = useState(LANDSCAPE_SLOTS[0].code)
  const [openBlocks, setOpenBlocks] = useState<Record<LandscapeBlock, boolean>>({ Z: true, U: false, P: false })
  const [showIntermediate, setShowIntermediate] = useState(false)

  const [activeRun, setActiveRun] = useState<LandscapeRun | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  const [replaceCode, setReplaceCode] = useState<string | null>(null)
  const [replacing, setReplacing] = useState(false)
  const [uploads, setUploads] = useState<Record<string, TrackedUpload>>({})
  const [runDetails, setRunDetails] = useState<Record<string, LandscapeRun | 'loading' | 'error'>>({})
  const [openRunId, setOpenRunId] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  const loadSlots = useCallback(async () => setSlots(await fetchLandscapeSlots()), [])
  const loadResults = useCallback(async () => setResults(await fetchLandscapeResults()), [])
  const loadRuns = useCallback(async () => {
    const list = await fetchLandscapeRuns(1, 20)
    setRuns(list)
    return list
  }, [])

  const loadAll = useCallback(async () => {
    setLoadError(null)
    try {
      const [, , list] = await Promise.all([loadSlots(), loadResults(), loadRuns()])
      // Продолжить отслеживание запуска после перезагрузки страницы.
      const stored = readStoredRun()
      const candidate = list.find((r) => r.id === stored) ?? list[0]
      if (candidate && isRunActive(candidate.status)) setActiveRun(candidate)
      else if (stored) storeRun(null)
    } catch (e) {
      setLoadError(apiErrorMessage(e, 'Не удалось получить данные ландшафтного планирования'))
    }
  }, [loadSlots, loadResults, loadRuns])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  // Переход из карточки сценария: /dashboard/landscape?variant=lp-v3-development.
  useEffect(() => {
    const v = new URLSearchParams(window.location.search).get('variant')
    if (v && LANDSCAPE_VARIANTS.some((x) => x.group === v)) {
      setSelectedResult(v)
      setTab('results')
    }
  }, [])

  // Пока какой-то вход обрабатывается сервером — периодически обновлять список.
  const slotsPending = (slots ?? []).some((s) => s.latest && !['ready', 'failed'].includes(s.latest.status))
  useEffect(() => {
    if (!slotsPending) return
    const t = setInterval(() => void loadSlots().catch(() => undefined), 5000)
    return () => clearInterval(t)
  }, [slotsPending, loadSlots])

  // ── Загруженные версии входов: ready → перечитать slots → сверить latest.id ──
  useEffect(() => setUploads(readStoredUploads()), [])

  const updateUploads = useCallback((fn: (u: Record<string, TrackedUpload>) => Record<string, TrackedUpload>) => {
    setUploads((prev) => {
      const next = fn(prev)
      storeUploads(next)
      return next
    })
  }, [])

  const processingKey = Object.values(uploads)
    .filter((u) => u.phase === 'processing')
    .map((u) => u.datasetId)
    .join(',')
  useEffect(() => {
    if (!processingKey) return
    let cancelled = false
    const check = async () => {
      const pending = Object.values(uploads).filter((u) => u.phase === 'processing')
      for (const u of pending) {
        try {
          const st = await fetchLandscapeDatasetStatus(u.datasetId)
          if (cancelled) return
          if (st.status === 'failed') {
            updateUploads((prev) => ({ ...prev, [u.code]: { ...u, phase: 'failed', error: st.parse_error } }))
          } else if (st.status === 'ready') {
            const fresh = await fetchLandscapeSlots()
            if (cancelled) return
            setSlots(fresh)
            const latest = fresh.find((x) => x.code === u.code)?.latest
            const ok = latest?.id === u.datasetId && latest?.status === 'ready'
            updateUploads((prev) => ({ ...prev, [u.code]: { ...u, phase: ok ? 'confirmed' : 'not-latest' } }))
          }
        } catch {
          /* временная ошибка сети — повторим на следующем опросе */
        }
      }
    }
    void check()
    const t = setInterval(() => void check(), 3000)
    return () => {
      cancelled = true
      clearInterval(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processingKey, updateUploads])

  const onUploaded = useCallback(
    (u: TrackedUpload) => updateUploads((prev) => ({ ...prev, [u.code]: u })),
    [updateUploads],
  )
  const dismissUpload = useCallback(
    (code: string) =>
      updateUploads((prev) => {
        const next = { ...prev }
        delete next[code]
        return next
      }),
    [updateUploads],
  )

  // ── Отслеживание запуска: 3 с первые 20 опросов, затем 10 с ─────────────────
  const activeRunId = activeRun && isRunActive(activeRun.status) ? activeRun.id : null
  useEffect(() => {
    if (!activeRunId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    let attempt = 0
    const tick = async () => {
      attempt++
      try {
        const run = await fetchLandscapeRun(activeRunId)
        if (cancelled) return
        setActiveRun(run)
        if (!isRunActive(run.status)) {
          storeRun(null)
          // После succeeded старые ссылки на COG/SLD не годятся — перечитать результаты.
          await Promise.all([loadRuns(), runPhase(run.status) === 'done' ? loadResults() : Promise.resolve()])
          if (runPhase(run.status) === 'done') {
            updateUploads((prev) =>
              Object.fromEntries(Object.entries(prev).filter(([, u]) => u.phase !== 'confirmed')),
            )
          }
          return
        }
      } catch (e) {
        if (cancelled) return
        setRunError(apiErrorMessage(e, 'Не удалось получить статус расчёта'))
      }
      if (!cancelled) timer = setTimeout(tick, attempt < 20 ? 3000 : 10000)
    }
    timer = setTimeout(tick, 3000)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [activeRunId, loadRuns, loadResults, updateUploads])

  const startRun = async (full: boolean) => {
    setMenuOpen(false)
    setRunError(null)
    setStarting(true)
    try {
      const run = await startLandscapeRun(full)
      storeRun(run.id)
      setActiveRun(run)
      setRuns((prev) => [run, ...(prev ?? []).filter((r) => r.id !== run.id)])
    } catch (e) {
      setRunError(apiErrorMessage(e, 'Не удалось запустить расчёт'))
    } finally {
      setStarting(false)
    }
  }

  // ── Производные ──────────────────────────────────────────────────────────
  const slotByCode = useMemo(() => new Map((slots ?? []).map((s) => [s.code, s])), [slots])
  const resultByGroup = useMemo(() => new Map((results ?? []).map((r) => [r.group, r])), [results])
  const readyCount = LANDSCAPE_SLOTS.filter((d) => slotByCode.get(d.code)?.latest?.status === 'ready').length
  const allReady = readyCount === LANDSCAPE_SLOTS.length
  const lastRun = activeRun ?? runs?.[0] ?? null
  const runInProgress = !!activeRunId

  const uploadList = Object.values(uploads)
  const recalcBlockedReason = !allReady
    ? 'Не все входные слои готовы'
    : replacing || uploadList.some((u) => u.phase === 'processing')
      ? 'Идёт замена входного слоя'
      : uploadList.some((u) => u.phase === 'not-latest')
        ? 'Загруженный файл не стал текущим входом'
        : runInProgress
          ? 'Расчёт уже выполняется'
          : null

  const currentResult = resultByGroup.get(selectedResult)
  const currentSlot = slotByCode.get(selectedSlot)

  const mapLayer: LandscapeMapLayer | null = useMemo(() => {
    if (tab === 'inputs') {
      const id = currentSlot?.latest?.status === 'ready' ? currentSlot.latest.id : null
      return id
        ? {
            datasetId: id,
            title: slotDef(selectedSlot)?.label ?? selectedSlot,
            sldUrl: datasetSldUrl(id),
            ramp: rampForCode(selectedSlot),
          }
        : null
    }
    const id = currentResult?.latest?.status === 'ready' ? currentResult.latest.id : null
    return id
      ? {
          datasetId: id,
          title: resultLabel(selectedResult),
          sldUrl: currentResult?.sld_url,
          ramp: rampForCode(selectedResult),
        }
      : null
  }, [tab, currentSlot, currentResult, selectedSlot, selectedResult])

  const toggleRun = async (id: string) => {
    if (openRunId === id) {
      setOpenRunId(null)
      return
    }
    setOpenRunId(id)
    if (runDetails[id] && runDetails[id] !== 'error') return
    setRunDetails((d) => ({ ...d, [id]: 'loading' }))
    try {
      const run = await fetchLandscapeRun(id)
      setRunDetails((d) => ({ ...d, [id]: run }))
    } catch {
      setRunDetails((d) => ({ ...d, [id]: 'error' }))
    }
  }

  // Текущие входы по группам — для сверки с фактическими входами запуска.
  const currentInputs = useMemo(
    () => new Map((slots ?? []).filter((x) => x.latest).map((x) => [x.code, x.latest!.id])),
    [slots],
  )

  const download = async () => {
    const ds = currentResult?.latest
    if (!ds) return
    setDownloadError(null)
    setDownloading(true)
    try {
      await waitRasterIngest(ds.id)
      await downloadLandscapeRaster(ds.id, `${selectedResult}_${ds.updated_at.slice(0, 10)}.tif`)
    } catch (e) {
      setDownloadError(apiErrorMessage(e, 'Не удалось скачать слой'))
    } finally {
      setDownloading(false)
    }
  }

  const onReplaceBusy = useCallback((busy: boolean) => setReplacing(busy), [])

  // ── Разметка ─────────────────────────────────────────────────────────────
  return (
    <div className="space-y-5 px-4 py-6">
      {/* Шапка */}
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <h1 className="text-2xl font-semibold text-gray-900">Ландшафтное планирование</h1>
          <p className="mt-1.5 text-sm text-gray-600">
            Комплексная оценка природной значимости, уязвимости, экологических ограничений,
            потенциальных пространственных конфликтов и вариантов функционального зонирования
            территории Северо-Казахстанской области. Расчёт выполняется на сервере по 13 входным слоям.
          </p>
          <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
            <Link href="/dashboard/landscape/compare" className="text-sm text-primary-700 hover:underline">
              Сравнение вариантов V1–V3 по критериям →
            </Link>
            <Link href="/dashboard/landscape/impact" className="text-sm text-primary-700 hover:underline">
              Оценка социально-экономического эффекта (ЗБ-УР) →
            </Link>
          </div>
        </div>

        <div className="flex flex-wrap items-stretch gap-3">
          <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
            <div className="text-xs text-gray-500">Входные данные</div>
            <div className={`mt-0.5 text-lg font-semibold ${allReady ? 'text-emerald-700' : 'text-amber-700'}`}>
              {slots ? `${readyCount} / ${LANDSCAPE_SLOTS.length} готовы` : '…'}
            </div>
          </div>
          <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
            <div className="text-xs text-gray-500">Последний расчёт</div>
            {lastRun ? (
              <div className="mt-0.5 flex items-center gap-2">
                <span className="text-sm font-medium text-gray-900">
                  {fmtDateTime(lastRun.finished_at ?? lastRun.started_at ?? lastRun.created_at)}
                </span>
                <RunBadge status={lastRun.status} />
              </div>
            ) : (
              <div className="mt-0.5 text-sm text-gray-500">{runs ? 'запусков нет' : '…'}</div>
            )}
            {activeRun && isRunActive(activeRun.status) && runProgress(activeRun) && (
              <div className="mt-0.5 text-xs text-gray-500">
                {(() => {
                  const pr = runProgress(activeRun)!
                  return `узлы ${pr.done} / ${pr.total}${pr.skipped ? `, переиспользовано ${pr.skipped}` : ''}`
                })()}
              </div>
            )}
            <button onClick={() => setTab('history')} className="mt-1 text-xs text-primary-700 hover:underline">
              История расчётов →
            </button>
          </div>

          {canEdit && (
            <div className="relative flex items-stretch">
              <button
                onClick={() => startRun(false)}
                disabled={!!recalcBlockedReason || starting}
                title={recalcBlockedReason ?? undefined}
                className="rounded-l-xl bg-primary-600 px-4 text-sm font-medium text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {runInProgress ? 'Расчёт выполняется…' : starting ? 'Запуск…' : 'Пересчитать ландшафтное планирование'}
              </button>
              <button
                onClick={() => setMenuOpen((v) => !v)}
                disabled={!!recalcBlockedReason || starting}
                className="rounded-r-xl border-l border-primary-500 bg-primary-600 px-2.5 text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Дополнительные действия"
              >
                ⋮
              </button>
              {menuOpen && (
                <div className="absolute right-0 top-full z-20 mt-1 w-64 rounded-lg border border-gray-200 bg-white p-1 shadow-lg">
                  <button onClick={() => startRun(true)} className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-gray-50">
                    <div className="font-medium text-gray-900">Полный перерасчёт</div>
                    <div className="text-xs text-gray-500">С первого этапа, без повторного использования результатов</div>
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </header>

      {loadError && <ErrorBar message={loadError} onRetry={loadAll} />}
      {runError && <ErrorBar message={runError} onRetry={() => setRunError(null)} />}
      {activeRun && runPhase(activeRun.status) === 'failed' && activeRun.error && (
        <ErrorBar message={`Расчёт завершился с ошибкой: ${activeRun.error}`} />
      )}
      {canEdit && recalcBlockedReason && !runInProgress && slots && (
        <p className="text-xs text-gray-500">Перерасчёт недоступен: {recalcBlockedReason.toLowerCase()}.</p>
      )}

      {/* Вкладки */}
      <nav className="flex flex-wrap gap-1 border-b border-gray-200">
        {([
          ['results', 'Результаты'],
          ['inputs', `Входные данные (${LANDSCAPE_SLOTS.length})`],
          ['history', 'История расчётов'],
        ] as Array<[Tab, string]>).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${
              tab === id ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      <section className={tab === 'history' ? 'hidden' : 'grid gap-5 lg:grid-cols-[minmax(0,400px)_1fr]'}>
        {/* Левая панель */}
        <div className="space-y-4">
          {tab === 'results' && (
            <>
              <div className="rounded-xl border border-gray-200 bg-white p-4">
                <h2 className="mb-3 text-sm font-semibold text-gray-900">Варианты функционального зонирования</h2>
                <div className="space-y-2">
                  {LANDSCAPE_VARIANTS.map((v) => {
                    const r = resultByGroup.get(v.group)
                    return (
                      <label
                        key={v.group}
                        className={`block cursor-pointer rounded-lg border p-3 transition ${
                          selectedResult === v.group ? 'border-primary-400 bg-primary-50' : 'border-gray-200 hover:border-gray-300'
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <input type="radio" name="lp-variant" checked={selectedResult === v.group} onChange={() => setSelectedResult(v.group)} />
                          <span className="text-sm font-medium text-gray-900">{v.label}</span>
                          {results && !r?.latest && <span className="text-xs text-amber-700">нет результата</span>}
                        </div>
                        <p className="mt-1 pl-6 text-xs text-gray-500">{v.hint}</p>
                      </label>
                    )
                  })}
                </div>
                <p className="mt-3 text-xs text-gray-400">
                  Стартовый вариант V2 — значение по умолчанию, а не оценка «лучшего» сценария.
                </p>
              </div>

              <div className="rounded-xl border border-gray-200 bg-white p-4">
                <h2 className="mb-3 text-sm font-semibold text-gray-900">Результат на карте</h2>
                {currentResult?.latest ? (
                  <>
                    <dl className="grid grid-cols-[90px_1fr] gap-y-1.5 text-sm">
                      <dt className="text-gray-500">Название</dt>
                      <dd className="text-gray-900">{resultLabel(selectedResult)}</dd>
                      <dt className="text-gray-500">Обновлён</dt>
                      <dd className="text-gray-900">{fmtDateTime(currentResult.latest.updated_at)}</dd>
                      <dt className="text-gray-500">Формат</dt>
                      <dd className="text-gray-900">COG (GeoTIFF)</dd>
                      {currentResult.latest.status !== 'ready' && (
                        <>
                          <dt className="text-gray-500">Статус</dt>
                          <dd className="text-amber-700">{currentResult.latest.status}</dd>
                        </>
                      )}
                    </dl>
                    <button
                      onClick={download}
                      disabled={downloading || currentResult.latest.status !== 'ready'}
                      className="mt-4 w-full rounded-lg border border-primary-300 px-4 py-2 text-sm font-medium text-primary-700 hover:bg-primary-50 disabled:opacity-50"
                    >
                      {downloading ? 'Подготовка файла…' : 'Скачать слой'}
                    </button>
                    {downloadError && <p className="mt-2 text-xs text-rose-700">{downloadError}</p>}
                  </>
                ) : (
                  <p className="text-sm text-gray-500">{results ? 'Результат ещё не рассчитан.' : 'Загрузка…'}</p>
                )}
              </div>

              <div className="rounded-xl border border-gray-200 bg-white">
                <button
                  onClick={() => setShowIntermediate((v) => !v)}
                  className="flex w-full items-center justify-between px-4 py-3 text-sm font-semibold text-gray-900"
                >
                  <span>Промежуточные расчётные слои</span>
                  <span className="text-gray-400">{showIntermediate ? '▲' : '▼'}</span>
                </button>
                {showIntermediate && (
                  <ul className="space-y-0.5 border-t border-gray-100 px-2 py-2">
                    {LANDSCAPE_INTERMEDIATE.map((l) => {
                      const has = !!resultByGroup.get(l.group)?.latest
                      return (
                        <li key={l.group}>
                          <button
                            onClick={() => setSelectedResult(l.group)}
                            disabled={!has}
                            className={`w-full rounded-md px-2 py-1.5 text-left text-sm disabled:text-gray-300 ${
                              selectedResult === l.group ? 'bg-primary-50 text-primary-800' : 'text-gray-700 hover:bg-gray-50'
                            }`}
                          >
                            {l.label}
                            <span className="ml-1 text-[11px] text-gray-400">{l.group}</span>
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            </>
          )}

          {tab === 'inputs' && (
            <>
              {LANDSCAPE_BLOCKS.map(({ block, title }) => {
                const defs = LANDSCAPE_SLOTS.filter((d) => d.block === block)
                const open = openBlocks[block]
                return (
                  <div key={block} className="rounded-xl border border-gray-200 bg-white">
                    <button
                      onClick={() => setOpenBlocks((o) => ({ ...o, [block]: !o[block] }))}
                      className="flex w-full items-center justify-between px-4 py-3 text-sm font-semibold text-gray-900"
                    >
                      <span>
                        {title} ({defs.length})
                      </span>
                      <span className="text-gray-400">{open ? '▲' : '▼'}</span>
                    </button>
                    {open && (
                      <ul className="divide-y divide-gray-100 border-t border-gray-100">
                        {defs.map((d) => {
                          const s = slotByCode.get(d.code)
                          const period = fmtPeriod(s?.latest?.start_date, s?.latest?.end_date)
                          return (
                            <li
                              key={d.code}
                              onClick={() => setSelectedSlot(d.code)}
                              className={`flex cursor-pointer items-center gap-3 px-4 py-2.5 ${
                                selectedSlot === d.code ? 'bg-primary-50' : 'hover:bg-gray-50'
                              }`}
                            >
                              <DatasetStatusIcon status={s?.latest?.status} />
                              <div className="min-w-0 flex-1">
                                <div className="text-sm leading-snug text-gray-900">{d.label}</div>
                                <div className="text-[11px] text-gray-400">
                                  {d.code}
                                  {period && <span className="ml-2 text-gray-500">период {period}</span>}
                                </div>
                                {uploads[d.code] && (
                                  <UploadNote u={uploads[d.code]} onDismiss={() => dismissUpload(d.code)} />
                                )}
                              </div>
                              <span className="shrink-0 text-xs text-gray-500" title="Дата обновления">
                                {fmtDate(s?.latest?.updated_at)}
                              </span>
                              {canEdit && (
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    setReplaceCode(d.code)
                                  }}
                                  disabled={!s || runInProgress}
                                  title={runInProgress ? 'Дождитесь завершения расчёта' : undefined}
                                  className="shrink-0 rounded-md border border-primary-200 px-2.5 py-1 text-xs font-medium text-primary-700 hover:bg-primary-50 disabled:opacity-40"
                                >
                                  Заменить
                                </button>
                              )}
                            </li>
                          )
                        })}
                      </ul>
                    )}
                  </div>
                )
              })}

              <details className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-xs text-gray-600">
                <summary className="cursor-pointer text-sm font-medium text-gray-800">Техническая информация о слое</summary>
                <dl className="mt-2 grid grid-cols-[90px_1fr] gap-y-1 break-all">
                  <dt className="text-gray-400">slot_code</dt>
                  <dd>{selectedSlot}</dd>
                  <dt className="text-gray-400">group_id</dt>
                  <dd>{currentSlot?.group_id ?? '—'}</dd>
                  <dt className="text-gray-400">dataset_id</dt>
                  <dd>{currentSlot?.latest?.id ?? '—'}</dd>
                  <dt className="text-gray-400">Файл</dt>
                  <dd className="break-normal">{currentSlot?.latest?.name ?? '—'}</dd>
                  <dt className="text-gray-400">Статус</dt>
                  <dd>{currentSlot?.latest?.status ?? '—'}</dd>
                </dl>
              </details>
            </>
          )}
        </div>

        {/* Карта: один экземпляр на обе вкладки, показывает один слой */}
        <div className="h-[560px] lg:h-auto lg:min-h-[640px]">
          <LandscapeRasterMap layer={mapLayer} />
          {!mapLayer && (slots || results) && (
            <p className="mt-2 text-xs text-gray-500">
              {tab === 'inputs'
                ? 'Выбранный входной слой ещё не готов к показу.'
                : 'Выбранный результат ещё не рассчитан.'}
            </p>
          )}
        </div>
      </section>

      {tab === 'history' && (
        <section className="overflow-x-auto rounded-xl border border-gray-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
              <tr>
                <th className="px-4 py-2 font-medium">Создан</th>
                <th className="px-4 py-2 font-medium">Режим</th>
                <th className="px-4 py-2 font-medium">Статус</th>
                <th className="px-4 py-2 font-medium">Завершён</th>
                <th className="px-4 py-2 font-medium">Длительность</th>
                <th className="px-4 py-2 font-medium">Ошибка</th>
                {canEdit && <th className="px-4 py-2 font-medium">Узлы</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(runs ?? []).map((r) => (
                <Fragment key={r.id}>
                <tr>
                  <td className="whitespace-nowrap px-4 py-2 text-gray-900">{fmtDateTime(r.created_at)}</td>
                  <td className="px-4 py-2 text-gray-700">{r.from_stage === 0 ? 'Полный' : 'Обычный'}</td>
                  <td className="px-4 py-2">
                    <RunBadge status={r.id === activeRun?.id ? activeRun.status : r.status} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-2 text-gray-700">{fmtDateTime(r.finished_at)}</td>
                  <td className="px-4 py-2 text-gray-700">{fmtDuration(r)}</td>
                  <td className="max-w-md px-4 py-2 text-xs text-rose-700">{r.error ?? ''}</td>
                  {canEdit && (
                    <td className="px-4 py-2">
                      <button onClick={() => void toggleRun(r.id)} className="text-xs text-primary-700 hover:underline">
                        {openRunId === r.id ? 'Скрыть' : 'Показать'}
                      </button>
                    </td>
                  )}
                </tr>
                {openRunId === r.id && (
                  <tr>
                    <td colSpan={7} className="bg-gray-50 px-4 py-3">
                      {runDetails[r.id] === 'loading' || !runDetails[r.id] ? (
                        <p className="text-xs text-gray-500">Загрузка…</p>
                      ) : runDetails[r.id] === 'error' ? (
                        <p className="text-xs text-rose-700">Не удалось получить узлы запуска.</p>
                      ) : (
                        <RunNodes run={runDetails[r.id] as LandscapeRun} currentInputs={currentInputs} />
                      )}
                    </td>
                  </tr>
                )}
                </Fragment>
              ))}
              {runs && runs.length === 0 && (
                <tr>
                  <td colSpan={canEdit ? 7 : 6} className="px-4 py-6 text-center text-gray-500">Запусков пока не было</td>
                </tr>
              )}
              {!runs && (
                <tr>
                  <td colSpan={canEdit ? 7 : 6} className="px-4 py-6 text-center text-gray-500">Загрузка…</td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      )}

      {replaceCode && (
        <ReplaceSlotModal
          slot={slotByCode.get(replaceCode)}
          tracked={uploads[replaceCode]}
          onUploaded={onUploaded}
          onClose={() => setReplaceCode(null)}
          onBusyChange={onReplaceBusy}
        />
      )}
    </div>
  )
}
