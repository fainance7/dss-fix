'use client'

import { useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useAuthStore } from '@/store/authStore'
import api from '@/lib/api'
import { signWithNCALayer, NCALayerError } from '@/lib/ncalayer'
import { ECPChallengeResponse } from '@/types/auth'

function getHomeByRole(roles: string[]): string {
  if (roles.includes('admin'))    return '/admin/monitoring'
  if (roles.includes('akim_ray')) return '/dashboard/tasks'
  if (roles.includes('exp_dir'))  return '/dashboard/map'
  if (roles.includes('exp_ver'))  return '/dashboard/map'
  if (roles.includes('akim_obl')) return '/dashboard'
  if (roles.includes('data_uploader')) return '/dashboard/data-intake'
  return '/dashboard'
}

export default function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const redirectParam = searchParams.get('redirect')

  const { login, loginEcp, isLoading, error } = useAuthStore()
  const [activeTab, setActiveTab] = useState<'password' | 'ecp'>('password')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [ecpStatus, setEcpStatus] = useState<string | null>(null)

  function redirectAfterLogin() {
    if (redirectParam) {
      router.push(redirectParam)
    } else {
      const user = useAuthStore.getState().user
      const roleNames = user?.roles.map((r) => r.name) ?? []
      router.push(getHomeByRole(roleNames))
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await login(username, password)
      redirectAfterLogin()
    } catch {
      // error shown via store.error
    }
  }

  const handleEcpLogin = async () => {
    useAuthStore.setState({ error: null })
    setEcpStatus('Получение задачи для подписи...')
    try {
      // 1. Get challenge from backend
      const { data: challenge } = await api.get<ECPChallengeResponse>('/auth/ecp-challenge')

      // 2. Sign the nonce with NCALayer
      setEcpStatus('Ожидание подписи в NCALayer...')
      const cmsSignature = await signWithNCALayer(challenge.nonce)

      // 3. Login with signed CMS
      setEcpStatus('Проверка сертификата...')
      await loginEcp(challenge.challenge_token, cmsSignature)

      setEcpStatus(null)
      redirectAfterLogin()
    } catch (err) {
      setEcpStatus(null)
      if (err instanceof NCALayerError) {
        useAuthStore.setState({ error: err.message, isLoading: false })
      } else {
        const message =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          (err instanceof Error ? err.message : 'Ошибка входа по ЭЦП')
        useAuthStore.setState({ error: message, isLoading: false })
      }
    }
  }

  return (
    <div className="space-y-4">
      {/* Tab switcher */}
      <div className="flex rounded-lg bg-gray-100 p-1">
        <button
          type="button"
          onClick={() => { setActiveTab('password'); useAuthStore.setState({ error: null }); setEcpStatus(null) }}
          className={`flex-1 rounded-md py-1.5 text-sm font-medium transition-colors ${
            activeTab === 'password'
              ? 'bg-white shadow text-gray-900'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          Пароль
        </button>
        <button
          type="button"
          onClick={() => { setActiveTab('ecp'); useAuthStore.setState({ error: null }); setEcpStatus(null) }}
          className={`flex-1 rounded-md py-1.5 text-sm font-medium transition-colors ${
            activeTab === 'ecp'
              ? 'bg-white shadow text-gray-900'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          ЭЦП (NCALayer)
        </button>
      </div>

      {/* Shared error display */}
      {error && (
        <div className="rounded-md bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Password tab */}
      {activeTab === 'password' && (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="username" className="block text-sm font-medium text-gray-700 mb-1">
              Имя пользователя
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              placeholder="admin"
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">
              Пароль
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              placeholder="••••••••"
            />
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full rounded-lg bg-primary-500 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading ? 'Вход...' : 'Войти'}
          </button>
        </form>
      )}

      {/* ECP tab */}
      {activeTab === 'ecp' && (
        <div className="space-y-4">
          <p className="text-sm text-gray-500">
            Убедитесь, что{' '}
            <span className="font-medium text-gray-700">NCALayer</span>{' '}
            запущен на вашем компьютере.
          </p>

          {ecpStatus && (
            <div className="rounded-md bg-blue-50 border border-blue-200 px-4 py-3 text-sm text-blue-700 flex items-center gap-2">
              <svg className="h-4 w-4 animate-spin shrink-0" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              {ecpStatus}
            </div>
          )}

          <button
            type="button"
            onClick={handleEcpLogin}
            disabled={isLoading}
            className="w-full rounded-lg bg-primary-500 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading ? 'Вход...' : 'Войти по ЭЦП'}
          </button>
        </div>
      )}
    </div>
  )
}
