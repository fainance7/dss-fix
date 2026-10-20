import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from 'axios'
import { markSignedOut } from '@/lib/session'

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? '/api/v1'

// Токены лежат в httpOnly-cookie API (аудит безопасности, H-1): JS их не видит,
// браузер прикладывает cookie сам — для этого нужен withCredentials.
const api: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
})

// Запросы, на 401 которых обновлять сеанс бессмысленно.
const NO_REFRESH = /\/auth\/(login|login-ecp|refresh|logout)\b/

// Один refresh на все параллельные запросы, получившие 401.
let refreshing: Promise<void> | null = null

export function refreshSession(): Promise<void> {
  refreshing ??= axios
    .post(`${BASE_URL}/auth/refresh`, null, { withCredentials: true })
    .then(() => undefined)
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

/** Автообновление access-токена на 401 и повтор запроса; не удалось — на страницу входа. */
export function attachRefresh(client: AxiosInstance): void {
  client.interceptors.response.use(
    (response) => response,
    async (error: AxiosError) => {
      const original = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined
      const url = original?.url ?? ''
      if (error.response?.status === 401 && original && !original._retry && !NO_REFRESH.test(url)) {
        original._retry = true
        try {
          await refreshSession()
          return client(original)
        } catch {
          markSignedOut()
          if (typeof window !== 'undefined') window.location.href = '/login'
        }
      }
      return Promise.reject(error)
    },
  )
}

attachRefresh(api)

export default api
