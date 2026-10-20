/**
 * NCALayer WebSocket client.
 *
 * NCALayer is a local Java application that exposes a WebSocket on 127.0.0.1:13579.
 * It provides access to the user's ЭЦП (electronic digital signature) tokens (Kaztoken, etc.).
 *
 * Usage:
 *   const cmsSignature = await signWithNCALayer(nonceBase64)
 */

import { NCALayerResponse } from '@/types/auth'

// NCALayer supports both wss:// (for HTTPS pages) and ws:// (for HTTP pages).
// Modern browsers block ws:// connections from HTTPS pages (mixed content policy).
// We try wss:// first, then fall back to ws:// for plain-HTTP dev environments.
const NCALAYER_URLS = ['wss://127.0.0.1:13579', 'ws://127.0.0.1:13579']
const CONNECT_TIMEOUT_MS = 3000
const SIGN_TIMEOUT_MS = 60000

export type NCALayerErrorCode = 'not_running' | 'canceled' | 'timeout' | 'sign_error'

export class NCALayerError extends Error {
  constructor(
    message: string,
    public readonly code: NCALayerErrorCode,
  ) {
    super(message)
    this.name = 'NCALayerError'
  }
}

/** Try to open a WebSocket connection to `url`. Resolves with the open socket or rejects on error/timeout. */
function tryConnect(url: string): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    let ws: WebSocket
    try {
      ws = new WebSocket(url)
    } catch {
      reject(new Error('create_failed'))
      return
    }

    const timer = setTimeout(() => {
      ws.close()
      reject(new Error('timeout'))
    }, CONNECT_TIMEOUT_MS)

    ws.onopen = () => { clearTimeout(timer); resolve(ws) }
    ws.onerror = () => { clearTimeout(timer); reject(new Error('error')) }
  })
}

/**
 * Connects to the local NCALayer WebSocket, prompts the user to sign
 * the given base64-encoded nonce with their ЭЦП certificate.
 *
 * Returns base64-encoded CMS SignedData on success.
 * Throws NCALayerError with a discriminated `code` on failure.
 */
export async function signWithNCALayer(nonceBase64: string): Promise<string> {
  // Try wss:// first (required for HTTPS pages), then ws:// for HTTP dev
  let ws: WebSocket | null = null
  for (const url of NCALAYER_URLS) {
    try {
      ws = await tryConnect(url)
      break
    } catch {
      // try next URL
    }
  }

  if (!ws) {
    throw new NCALayerError('Установите и запустите NCALayer', 'not_running')
  }

  const socket = ws
  return new Promise((resolve, reject) => {
    let settled = false

    const settle = (fn: () => void) => {
      if (!settled) {
        settled = true
        fn()
      }
    }

    const signTimer = setTimeout(() => {
      socket.close()
      settle(() => reject(new NCALayerError('Время ожидания подписи истекло', 'timeout')))
    }, SIGN_TIMEOUT_MS)

    socket.onmessage = (event: MessageEvent) => {
      let raw: unknown
      try {
        raw = JSON.parse(event.data as string)
      } catch {
        clearTimeout(signTimer)
        socket.close()
        settle(() => reject(new NCALayerError('Некорректный ответ от NCALayer', 'sign_error')))
        return
      }

      // NCALayer sends a version handshake on connect — ignore it and wait for the real response
      if (
        raw != null &&
        typeof raw === 'object' &&
        'result' in (raw as object) &&
        typeof (raw as { result?: unknown }).result === 'object' &&
        (raw as { result: Record<string, unknown> }).result !== null &&
        'version' in ((raw as { result: Record<string, unknown> }).result)
      ) {
        return // handshake message, wait for actual sign response
      }

      clearTimeout(signTimer)
      socket.close()

      // Normalise: some NCALayer versions wrap the payload in a "result" field
      const response = (
        raw != null &&
        typeof raw === 'object' &&
        'result' in (raw as object) &&
        typeof (raw as NCALayerResponse & { result: unknown }).result === 'object'
          ? (raw as { result: NCALayerResponse }).result
          : (raw as NCALayerResponse)
      )

      if (response.message === 'action.canceled') {
        settle(() => reject(new NCALayerError('Подписание отменено пользователем', 'canceled')))
        return
      }

      // Success: responseObject present (check before errorCode to handle version quirks)
      if (response.responseObject) {
        settle(() => resolve(response.responseObject!))
        return
      }

      // Also handle flat string result (old NCALayer versions)
      if (typeof (raw as { result?: unknown })?.result === 'string') {
        settle(() => resolve((raw as { result: string }).result))
        return
      }

      settle(() =>
        reject(
          new NCALayerError(
            `Ошибка NCALayer: ${response.message ?? response.errorCode ?? JSON.stringify(raw)}`,
            'sign_error',
          ),
        ),
      )
    }

    socket.onerror = () => {
      clearTimeout(signTimer)
      settle(() => reject(new NCALayerError('Соединение с NCALayer прервано', 'not_running')))
    }

    socket.onclose = (event: CloseEvent) => {
      if (!event.wasClean) {
        clearTimeout(signTimer)
        settle(() => reject(new NCALayerError('Соединение с NCALayer прервано', 'not_running')))
      }
    }

    socket.send(
      JSON.stringify({
        module: 'kz.gov.pki.knca.commonUtils',
        method: 'createCMSSignatureFromBase64',
        args: ['PKCS12', 'SIGNATURE', nonceBase64, true],
      }),
    )
  })
}
