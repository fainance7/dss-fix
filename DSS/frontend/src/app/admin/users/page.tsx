'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import api from '@/lib/api'
import { useAuthStore } from '@/store/authStore'
import { UserCreate, UserOut, UserListResponse, UserUpdate } from '@/types/user'
import Header from '@/components/layout/Header'
import UserTable from '@/components/users/UserTable'
import UserModal from '@/components/users/UserModal'

export default function UsersPage() {
  const router = useRouter()
  const { hasRole, isAuthenticated, user, fetchMe } = useAuthStore()

  const [users, setUsers] = useState<UserOut[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [isLoading, setIsLoading] = useState(true)
  const [modalUser, setModalUser] = useState<UserOut | null | 'new'>('new')
  const [showModal, setShowModal] = useState(false)

  // Reset password modal state
  const [resetTarget, setResetTarget] = useState<UserOut | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [resetSaving, setResetSaving] = useState(false)
  const [resetError, setResetError] = useState<string | null>(null)

  useEffect(() => {
    if (!isAuthenticated) { router.replace('/login'); return }
    if (!user) { fetchMe() }
  }, [isAuthenticated, user, fetchMe, router])

  const isAdmin = hasRole('admin')

  useEffect(() => {
    if (!isAdmin && user) router.replace('/dashboard')
  }, [isAdmin, user, router])

  const loadUsers = useCallback(async () => {
    setIsLoading(true)
    try {
      const { data } = await api.get<UserListResponse>(`/users?page=${page}&page_size=20`)
      setUsers(data.items)
      setTotal(data.total)
      setPages(data.pages)
    } finally {
      setIsLoading(false)
    }
  }, [page])

  useEffect(() => {
    if (isAdmin) loadUsers()
  }, [isAdmin, loadUsers])

  const handleSave = async (data: UserCreate | UserUpdate): Promise<UserOut> => {
    let saved: UserOut
    if (modalUser === 'new' || modalUser === null) {
      const { data: created } = await api.post<UserOut>('/users', data)
      saved = created
    } else {
      const { data: updated } = await api.patch<UserOut>(`/users/${(modalUser as UserOut).id}`, data)
      saved = updated
    }
    await loadUsers()
    return saved
  }

  const handleDelete = async (user: UserOut) => {
    if (!confirm(`Удалить пользователя «${user.username}»?`)) return
    await api.delete(`/users/${user.id}`)
    await loadUsers()
  }

  const handleResetPasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!resetTarget) return
    setResetSaving(true)
    setResetError(null)
    try {
      await api.patch(`/users/${resetTarget.id}`, { password: resetPassword })
      setResetTarget(null)
      setResetPassword('')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Ошибка сброса пароля'
      setResetError(msg)
    } finally {
      setResetSaving(false)
    }
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header title="Управление пользователями" />
        <main className="flex-1 overflow-y-auto bg-gray-50 p-6">
          <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-100">
            {/* Toolbar */}
            <div className="flex items-center justify-between mb-6">
              <div className="text-sm text-gray-500">
                Всего: <span className="font-semibold text-gray-900">{total}</span>
              </div>
              <button
                onClick={() => { setModalUser('new'); setShowModal(true) }}
                className="rounded-lg bg-primary-500 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-600 transition-colors"
              >
                + Добавить пользователя
              </button>
            </div>

            <UserTable
              users={users}
              isLoading={isLoading}
              onEdit={(u) => { setModalUser(u); setShowModal(true) }}
              onDelete={handleDelete}
              onResetPassword={(u) => { setResetTarget(u); setResetPassword(''); setResetError(null) }}
            />

            {/* Pagination */}
            {pages > 1 && (
              <div className="flex items-center justify-center gap-2 mt-6">
                <button
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                  className="rounded-md border border-gray-300 px-3 py-1 text-sm disabled:opacity-40"
                >
                  ←
                </button>
                <span className="text-sm text-gray-600">
                  {page} / {pages}
                </span>
                <button
                  disabled={page >= pages}
                  onClick={() => setPage((p) => p + 1)}
                  className="rounded-md border border-gray-300 px-3 py-1 text-sm disabled:opacity-40"
                >
                  →
                </button>
              </div>
            )}
          </div>
        </main>
      </div>

      {showModal && (
        <UserModal
          user={modalUser === 'new' ? null : (modalUser as UserOut)}
          onClose={() => { setShowModal(false); loadUsers() }}
          onSave={handleSave}
        />
      )}

      {/* Reset password modal */}
      {resetTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="w-full max-w-sm rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between px-6 py-5 border-b border-gray-100">
              <h2 className="text-base font-semibold text-gray-900">Сброс пароля</h2>
              <button
                onClick={() => setResetTarget(null)}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none"
              >
                ×
              </button>
            </div>

            <form onSubmit={handleResetPasswordSubmit} className="px-6 py-4 space-y-4">
              <p className="text-sm text-gray-500">
                Установить новый пароль для пользователя{' '}
                <span className="font-medium text-gray-900">{resetTarget.username}</span>
              </p>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Новый пароль</label>
                <input
                  type="password"
                  required
                  minLength={8}
                  value={resetPassword}
                  onChange={(e) => setResetPassword(e.target.value)}
                  placeholder="мин. 8 символов"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>

              {resetError && (
                <p className="text-sm text-red-600">{resetError}</p>
              )}

              <div className="flex justify-end gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => setResetTarget(null)}
                  className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                >
                  Отмена
                </button>
                <button
                  type="submit"
                  disabled={resetSaving}
                  className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-600 disabled:opacity-50"
                >
                  {resetSaving ? 'Сохранение...' : 'Установить пароль'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
