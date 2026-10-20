/**
 * Признак входа для middleware страниц.
 *
 * Сами токены — в httpOnly-cookie домена API, страницы фронта их не видят. Чтобы
 * middleware мог не пускать на закрытые страницы без входа, после входа ставится
 * отдельная cookie-отметка без секрета: подделка отметки даёт только пустую
 * страницу — все данные API всё равно требуют действительного токена.
 */
export const SESSION_MARK = 'dss_session'
const MAX_AGE = 60 * 60 * 24 * 7 // как у refresh-токена

export function markSignedIn(): void {
  if (typeof document === 'undefined') return
  const secure = window.location.protocol === 'https:' ? '; Secure' : ''
  document.cookie = `${SESSION_MARK}=1; path=/; max-age=${MAX_AGE}; SameSite=Strict${secure}`
}

export function markSignedOut(): void {
  if (typeof document === 'undefined') return
  document.cookie = `${SESSION_MARK}=; path=/; max-age=0; SameSite=Strict`
  // Следы прежней схемы, где токены хранились в браузере.
  try {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
  } catch {
    /* хранилище недоступно */
  }
  document.cookie = 'access_token=; path=/; max-age=0; SameSite=Strict'
}

/** Карта MapLibre грузит тайлы сама, вне axios: запросам к нашему API нужны cookie. */
export function withApiCredentials(url: string): { url: string; credentials?: 'include' } {
  const base = process.env.NEXT_PUBLIC_API_URL ?? '/api/v1'
  return url.startsWith(base) || url.includes('/api/v1/') ? { url, credentials: 'include' } : { url }
}
