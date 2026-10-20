'use client'

import { useState } from 'react'
import { useAuthStore } from '@/store/authStore'
import api from '@/lib/api'

const ROLE_LABELS: Record<string, string> = {
  admin: 'Администратор',
  exp_ver: 'Эксперт по проверке данных',
  exp_dir: 'Эксперт по направлениям',
  akim_obl: 'Акимат области',
  akim_ray: 'Акимат района',
  data_uploader: 'Загрузчик данных',
}

export default function ProfilePage() {
  const { user, fetchMe } = useAuthStore()

  const [email, setEmail] = useState(user?.email ?? '')
  const [iin, setIin] = useState('')
  const [profileSaving, setProfileSaving] = useState(false)
  const [profileSuccess, setProfileSuccess] = useState(false)
  const [profileError, setProfileError] = useState<string | null>(null)

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [pwdSaving, setPwdSaving] = useState(false)
  const [pwdSuccess, setPwdSuccess] = useState(false)
  const [pwdError, setPwdError] = useState<string | null>(null)

  async function handleProfileSave(e: React.FormEvent) {
    e.preventDefault()
    setProfileSaving(true)
    setProfileError(null)
    setProfileSuccess(false)
    try {
      const body: Record<string, string> = {}
      if (email !== user?.email) body.email = email
      if (iin) body.iin = iin
      if (Object.keys(body).length === 0) {
        setProfileSuccess(true)
        return
      }
      await api.patch('/auth/me', body)
      await fetchMe()
      setIin('')
      setProfileSuccess(true)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Ошибка сохранения'
      setProfileError(msg)
    } finally {
      setProfileSaving(false)
    }
  }

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault()
    if (newPassword !== confirmPassword) {
      setPwdError('Пароли не совпадают')
      return
    }
    if (newPassword.length < 8) {
      setPwdError('Новый пароль должен быть не менее 8 символов')
      return
    }
    setPwdSaving(true)
    setPwdError(null)
    setPwdSuccess(false)
    try {
      await api.post('/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setPwdSuccess(true)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Ошибка смены пароля'
      setPwdError(msg)
    } finally {
      setPwdSaving(false)
    }
  }

  if (!user) return null

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Мой профиль</h1>
        <p className="text-sm text-gray-500 mt-0.5">Просмотр и редактирование личных данных</p>
      </div>

      {/* Profile info */}
      <div className="rounded-xl border border-gray-200 bg-white p-6">
        <h2 className="text-sm font-semibold text-gray-700 mb-4">Основная информация</h2>

        {/* Roles */}
        <div className="mb-4 flex flex-wrap gap-2">
          {user.roles.map((r, i) => (
            <span
              key={i}
              className="inline-flex items-center rounded-full bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 ring-1 ring-primary-200"
            >
              {ROLE_LABELS[r.name] ?? r.name}
              {r.region_name && <span className="ml-1 text-primary-400">· {r.region_name}</span>}
            </span>
          ))}
        </div>

        <form onSubmit={handleProfileSave} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Имя пользователя</label>
            <input
              type="text"
              value={user.username}
              disabled
              className="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500 cursor-not-allowed"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              ИИН <span className="text-gray-400 font-normal text-xs">(оставьте пустым, чтобы не менять)</span>
            </label>
            <input
              type="text"
              inputMode="numeric"
              maxLength={12}
              value={iin}
              onChange={(e) => setIin(e.target.value.replace(/\D/g, '').slice(0, 12))}
              placeholder="000000000000"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm font-mono focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </div>

          {profileError && (
            <p className="text-sm text-red-600">{profileError}</p>
          )}
          {profileSuccess && (
            <p className="text-sm text-green-600">Профиль сохранён</p>
          )}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={profileSaving}
              className="rounded-lg bg-primary-500 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-600 disabled:opacity-50"
            >
              {profileSaving ? 'Сохранение...' : 'Сохранить'}
            </button>
          </div>
        </form>
      </div>

      {/* Change password */}
      <div className="rounded-xl border border-gray-200 bg-white p-6">
        <h2 className="text-sm font-semibold text-gray-700 mb-4">Смена пароля</h2>

        <form onSubmit={handleChangePassword} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Текущий пароль</label>
            <input
              type="password"
              required
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Новый пароль</label>
              <input
                type="password"
                required
                minLength={8}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="мин. 8 символов"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Повторите пароль</label>
              <input
                type="password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
            </div>
          </div>

          {pwdError && (
            <p className="text-sm text-red-600">{pwdError}</p>
          )}
          {pwdSuccess && (
            <p className="text-sm text-green-600">Пароль успешно изменён</p>
          )}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={pwdSaving}
              className="rounded-lg bg-primary-500 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-600 disabled:opacity-50"
            >
              {pwdSaving ? 'Сохранение...' : 'Изменить пароль'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
