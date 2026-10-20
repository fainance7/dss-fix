'use client'

/**
 * Краткий вывод ИИ по вкладке дашборда направления. Текст формирует локальная
 * LLM по показателям вкладки; он хранится на сервере и пересоздаётся только
 * при появлении новых данных, поэтому обычно отдаётся сразу.
 */

import { useEffect, useMemo, useState } from 'react'
import { usePathname } from 'next/navigation'
import { fetchDashboardSummary, type DashboardSummary } from '@/lib/api/ai'

const POLL_MS = 5000
const MAX_POLLS = 60

export default function TabSummary({
  tabId,
  title,
  directionCode,
  indicatorIds,
}: {
  /** Вкладка в пределах страницы; ключ вывода — «страница:вкладка». */
  tabId: string
  /** Название дашборда и вкладки — контекст для ИИ. */
  title: string
  directionCode: string
  /** Показатели, показанные на вкладке (ненайденные — null/undefined). */
  indicatorIds: (string | null | undefined)[]
}) {
  const pathname = usePathname()
  const ids = useMemo(
    () => Array.from(new Set(indicatorIds.filter((x): x is string => Boolean(x)))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [indicatorIds.join(',')],
  )
  const pageKey = `${pathname.replace(/^\/dashboard\/?/, '') || 'overview'}:${tabId}`
    .toLowerCase()
    .replace(/[^a-z0-9_:/-]/g, '-')
  const [summary, setSummary] = useState<DashboardSummary | null>(null)

  useEffect(() => {
    setSummary(null)
    if (ids.length === 0) return
    let cancel = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let polls = 0
    const load = () => {
      fetchDashboardSummary({
        page_key: pageKey,
        title,
        direction_code: directionCode,
        indicator_ids: ids,
      })
        .then((s) => {
          if (cancel) return
          setSummary(s)
          if (s.status === 'running' && polls < MAX_POLLS) {
            polls += 1
            timer = setTimeout(load, POLL_MS)
          }
        })
        .catch(() => {
          // Вывод — дополнение к дашборду: при ошибке или отказе в доступе блок не показываем.
          if (!cancel) setSummary(null)
        })
    }
    load()
    return () => {
      cancel = true
      if (timer) clearTimeout(timer)
    }
  }, [pageKey, title, directionCode, ids])

  if (!summary || summary.status === 'empty') return null
  const running = summary.status === 'running'

  return (
    <section className="rounded-xl bg-indigo-50 px-5 py-4 ring-1 ring-indigo-100">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-indigo-700">Вывод ИИ</h3>
        <span className="text-sm text-indigo-400">
          {running
            ? summary.text
              ? 'данные обновились — вывод пересчитывается…'
              : 'формируется…'
            : summary.generated_at
              ? `сформирован ${new Date(summary.generated_at).toLocaleDateString('ru-RU')} по данным вкладки`
              : ''}
        </span>
      </div>
      {summary.text ? (
        <p className="mt-2 text-base leading-relaxed text-gray-800">{summary.text}</p>
      ) : (
        <div className="mt-3 space-y-2">
          <div className="h-3.5 w-full animate-pulse rounded bg-indigo-100" />
          <div className="h-3.5 w-4/5 animate-pulse rounded bg-indigo-100" />
        </div>
      )}
    </section>
  )
}
