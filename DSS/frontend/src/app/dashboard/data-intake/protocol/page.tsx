'use client'

import { useEffect, useState } from 'react'
import api from '@/lib/api'
import { sanitizeHtml } from '@/lib/sanitize'

type ProtocolResp = { html: string; markdown: string }

export default function ProtocolPage() {
  const [resp, setResp] = useState<ProtocolResp | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<ProtocolResp>('/data-intake/protocol')
      .then((r) => setResp(r.data))
      .catch((e) => {
        const err = e as { response?: { data?: { detail?: string } } }
        setError(err.response?.data?.detail ?? 'Не удалось загрузить протокол')
      })
  }, [])

  const handleDownloadMd = async () => {
    try {
      const r = await api.get('/data-intake/protocol?format=md', { responseType: 'blob' })
      const blob = new Blob([r.data], { type: 'text/markdown' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'data_intake_protocol.md'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      alert('Не удалось скачать .md')
    }
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Протокол приёма исторических данных</h1>
        <button
          type="button"
          onClick={handleDownloadMd}
          className="rounded bg-gray-700 px-3 py-1.5 text-sm text-white"
        >
          Скачать .md
        </button>
      </div>

      {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      {resp && (
        <article
          className="md-body rounded-lg border bg-white p-6"
          dangerouslySetInnerHTML={{ __html: sanitizeHtml(resp.html) }}
        />
      )}
    </div>
  )
}
