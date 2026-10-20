import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import api from '@/lib/api'
import { markSignedIn, markSignedOut } from '@/lib/session'
import { MeResponse, RoleName } from '@/types/auth'

interface AuthState {
  user: MeResponse | null
  // Токены — в httpOnly-cookie API (H-1); в браузере хранится только признак входа.
  isAuthenticated: boolean
  isLoading: boolean
  error: string | null
  _hasHydrated: boolean

  login: (username: string, password: string) => Promise<void>
  loginEcp: (challengeToken: string, cmsSignature: string) => Promise<void>
  logout: () => Promise<void>
  fetchMe: () => Promise<void>
  hasRole: (role: RoleName) => boolean
  hasDirection: (directionId: string) => boolean
  isDirectionRestricted: () => boolean
  setHasHydrated: (v: boolean) => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      isAuthenticated: false,
      isLoading: false,
      error: null,
      _hasHydrated: false,
      setHasHydrated: (v) => set({ _hasHydrated: v }),

      login: async (username, password) => {
        set({ isLoading: true, error: null })
        try {
          await api.post('/auth/login', { username, password }, { headers: { 'X-Auth-Mode': 'cookie' } })
          markSignedIn()
          set({ isAuthenticated: true })
          await get().fetchMe()
        } catch (err: unknown) {
          const message =
            (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
            'Login failed'
          set({ error: message, isLoading: false })
          throw err
        }
      },

      loginEcp: async (challengeToken, cmsSignature) => {
        set({ isLoading: true, error: null })
        try {
          await api.post(
            '/auth/login-ecp',
            { challenge_token: challengeToken, cms_signature: cmsSignature },
            { headers: { 'X-Auth-Mode': 'cookie' } },
          )
          markSignedIn()
          set({ isAuthenticated: true })
          await get().fetchMe()
        } catch (err: unknown) {
          const message =
            (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
            'Ошибка входа по ЭЦП'
          set({ error: message, isLoading: false })
          throw err
        }
      },

      logout: async () => {
        // Сервер удаляет cookie и отзывает все токены пользователя (M-7).
        try {
          await api.post('/auth/logout')
        } catch {
          /* сеанс уже недействителен — выходим локально */
        }
        markSignedOut()
        set({ user: null, isAuthenticated: false, error: null })
      },

      fetchMe: async () => {
        try {
          const { data } = await api.get<MeResponse>('/auth/me')
          set({ user: data, isLoading: false })
        } catch {
          set({ user: null, isLoading: false })
        }
      },

      hasRole: (role) => {
        const user = get().user
        return user?.roles.some((r) => r.name === role) ?? false
      },

      hasDirection: (directionId) => {
        const user = get().user
        if (!user) return false
        // If no direction restrictions, user has access to all
        if (!user.directions || user.directions.length === 0) return true
        return user.directions.some((d) => d.id === directionId)
      },

      isDirectionRestricted: () => {
        const user = get().user
        return (user?.directions?.length ?? 0) > 0
      },
    }),
    {
      name: 'dss-auth',
      version: 2,
      partialize: (state) => ({ isAuthenticated: state.isAuthenticated }),
      // v1 хранил сами токены в localStorage — при обновлении они удаляются,
      // пользователь входит заново и получает httpOnly-cookie.
      migrate: () => {
        markSignedOut()
        return { isAuthenticated: false } as unknown as AuthState
      },
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true)
      },
    },
  ),
)
