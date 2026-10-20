'use client'

import { useEffect, useState } from 'react'
import api from '@/lib/api'
import { UserCreate, UserOut, UserUpdate } from '@/types/user'
import { fetchRegions, fetchDirections, assignUserDirections } from '@/lib/api/backend'
import type { Region } from '@/lib/api/backend'
import type { Direction } from '@/types/direction'

interface UserModalProps {
  user: UserOut | null
  onClose: () => void
  onSave: (data: UserCreate | UserUpdate) => Promise<UserOut>
}

const ROLE_LABELS: Record<string, string> = {
  admin: 'Администратор',
  exp_ver: 'Эксперт по проверке данных',
  exp_dir: 'Эксперт по направлениям',
  akim_obl: 'Акимат области',
  akim_ray: 'Акимат района',
  data_uploader: 'Загрузчик данных',
}

const AVAILABLE_ROLES = Object.keys(ROLE_LABELS)

export default function UserModal({ user, onClose, onSave }: UserModalProps) {
  const isEdit = !!user

  // Basic fields
  const [username, setUsername] = useState(user?.username ?? '')
  const [email, setEmail] = useState(user?.email ?? '')
  const [iin, setIin] = useState(user?.iin ?? '')
  const [password, setPassword] = useState('')
  const [isActive, setIsActive] = useState(user?.is_active ?? true)

  // Role management
  const [addRoleName, setAddRoleName] = useState('')
  const [addRegionId, setAddRegionId] = useState('')
  const [regions, setRegions] = useState<Region[]>([])
  const [currentRoles, setCurrentRoles] = useState(user?.roles ?? [])
  const [roleLoading, setRoleLoading] = useState(false)

  // Directions management (only for exp_dir)
  const [allDirections, setAllDirections] = useState<Direction[]>([])
  const [selectedDirIds, setSelectedDirIds] = useState<Set<string>>(
    new Set(user?.directions?.map((d) => d.id) ?? [])
  )
  const [dirLoading, setDirLoading] = useState(false)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const hasExpDir = currentRoles.some((r) => r.role_name === 'exp_dir')

  useEffect(() => {
    fetchRegions()
      .then((r) => setRegions(r.filter((reg: Region) => reg.level === 2)))
      .catch(() => {})
    fetchDirections()
      .then(setAllDirections)
      .catch(() => {})
  }, [])

  useEffect(() => {
    setUsername(user?.username ?? '')
    setEmail(user?.email ?? '')
    setIin(user?.iin ?? '')
    setPassword('')
    setIsActive(user?.is_active ?? true)
    setCurrentRoles(user?.roles ?? [])
    setSelectedDirIds(new Set(user?.directions?.map((d) => d.id) ?? []))
    setAddRoleName('')
    setAddRegionId('')
    setError(null)
  }, [user])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const data = isEdit
        ? ({
            ...(username !== user!.username ? { username } : {}),
            ...(email !== user!.email ? { email } : {}),
            ...(password ? { password } : {}),
            ...(iin !== (user!.iin ?? '') ? { iin: iin || undefined } : {}),
            is_active: isActive,
          } as UserUpdate)
        : ({ username, email, password, is_active: isActive, ...(iin ? { iin } : {}) } as UserCreate)

      const savedUser = await onSave(data)

      // For new user: assign role if selected
      if (!isEdit && addRoleName) {
        await api.post(`/users/${savedUser.id}/roles`, {
          role_name: addRoleName,
          region_id: addRegionId || null,
        })
      }

      // Save direction restrictions for exp_dir users
      const rolesAfterSave = isEdit ? currentRoles : (addRoleName ? [{ role_name: addRoleName }] : [])
      const willHaveExpDir = rolesAfterSave.some((r) => r.role_name === 'exp_dir')
      if (willHaveExpDir) {
        const dirIds = Array.from(selectedDirIds)
        // If all selected or none selected, send empty (= unrestricted)
        const idsToSend = dirIds.length === allDirections.length ? [] : dirIds
        await assignUserDirections(savedUser.id, idsToSend)
      }

      onClose()
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Ошибка сохранения'
      setError(message)
    } finally {
      setSaving(false)
    }
  }

  async function handleAddRole() {
    if (!addRoleName || !user) return
    setRoleLoading(true)
    setError(null)
    const regionName = addRegionId ? (regions.find((r) => r.id === addRegionId)?.name ?? null) : null
    try {
      await api.post<UserOut>(`/users/${user.id}/roles`, {
        role_name: addRoleName,
        region_id: addRegionId || null,
      })
      setCurrentRoles((prev) => [
        ...prev,
        {
          role_name: addRoleName,
          region_id: addRegionId || null,
          region_name: regionName,
          assigned_at: new Date().toISOString(),
        },
      ])
      setAddRoleName('')
      setAddRegionId('')
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Ошибка назначения роли'
      setError(message)
    } finally {
      setRoleLoading(false)
    }
  }

  async function handleRemoveRole(roleName: string, regionId: string | null) {
    if (!user) return
    setRoleLoading(true)
    setError(null)
    try {
      const params = regionId ? `?region_id=${regionId}` : ''
      await api.delete(`/users/${user.id}/roles/${roleName}${params}`)
      setCurrentRoles((prev) =>
        prev.filter((r) => !(r.role_name === roleName && r.region_id === regionId))
      )
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Ошибка удаления роли'
      setError(message)
    } finally {
      setRoleLoading(false)
    }
  }

  const needsRegion = (role: string) => role === 'akim_ray'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-5 border-b border-gray-100">
          <h2 className="text-base font-semibold text-gray-900">
            {isEdit ? 'Редактировать пользователя' : 'Новый пользователь'}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">
            ×
          </button>
        </div>

        {error && (
          <div className="mx-6 mt-4 rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div className="px-6 py-4 space-y-4">
            {/* Basic fields */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Имя пользователя</label>
                <input
                  type="text"
                  required
                  minLength={3}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
                <input
                  type="email"
                  required={!isEdit}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Пароль{isEdit && <span className="text-gray-400 font-normal text-xs ml-1">(пусто = не менять)</span>}
                </label>
                <input
                  type="password"
                  required={!isEdit}
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={isEdit ? '••••••••' : 'мин. 8 символов'}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  ИИН <span className="text-gray-400 font-normal text-xs">(для ЭЦП)</span>
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
            </div>

            <div className="flex items-center gap-2">
              <input
                id="is_active"
                type="checkbox"
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
              />
              <label htmlFor="is_active" className="text-sm font-medium text-gray-700">
                Активен {!isActive && <span className="text-red-500 font-normal">(пользователь заблокирован)</span>}
              </label>
            </div>

            {/* Roles section */}
            <div className="border-t border-gray-100 pt-4">
              <p className="text-sm font-medium text-gray-700 mb-3">Роли</p>

              {/* Current roles (edit mode) */}
              {isEdit && (
                <div className="mb-3 flex flex-wrap gap-2 min-h-[28px]">
                  {currentRoles.length === 0 && (
                    <span className="text-sm text-gray-400">Роли не назначены</span>
                  )}
                  {currentRoles.map((r) => (
                    <span
                      key={r.role_name + (r.region_id ?? '')}
                      className="inline-flex items-center gap-1.5 rounded-full bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700"
                    >
                      {ROLE_LABELS[r.role_name] ?? r.role_name}
                      {r.region_name && <span className="text-primary-400">· {r.region_name}</span>}
                      <button
                        type="button"
                        onClick={() => handleRemoveRole(r.role_name, r.region_id)}
                        disabled={roleLoading}
                        className="ml-0.5 text-primary-400 hover:text-red-500 leading-none disabled:opacity-40"
                        title="Удалить роль"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              )}

              {/* Add role */}
              <div className="flex gap-2 flex-wrap">
                <select
                  value={addRoleName}
                  onChange={(e) => { setAddRoleName(e.target.value); setAddRegionId('') }}
                  className="flex-1 min-w-[160px] rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                >
                  <option value="">— Выбрать роль —</option>
                  {AVAILABLE_ROLES.map((r) => (
                    <option key={r} value={r}>{ROLE_LABELS[r]}</option>
                  ))}
                </select>

                {needsRegion(addRoleName) && (
                  <select
                    value={addRegionId}
                    onChange={(e) => setAddRegionId(e.target.value)}
                    required={!isEdit}
                    className="flex-1 min-w-[140px] rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                  >
                    <option value="">— Район СКО —</option>
                    {regions.map((reg) => (
                      <option key={reg.id} value={reg.id}>{reg.name}</option>
                    ))}
                  </select>
                )}

                {isEdit && (
                  <button
                    type="button"
                    onClick={handleAddRole}
                    disabled={!addRoleName || roleLoading || (needsRegion(addRoleName) && !addRegionId)}
                    className="rounded-lg bg-gray-100 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {roleLoading ? '...' : '+ Добавить'}
                  </button>
                )}
              </div>
              {!isEdit && addRoleName && (
                <p className="mt-1.5 text-xs text-gray-400">
                  Роль будет назначена сразу после создания пользователя
                </p>
              )}
            </div>

            {/* Directions section (only for exp_dir) */}
            {hasExpDir && allDirections.length > 0 && (
              <div className="border-t border-gray-100 pt-4">
                <p className="text-sm font-medium text-gray-700 mb-1">Направления</p>
                <p className="text-xs text-gray-400 mb-3">
                  {selectedDirIds.size === 0
                    ? 'Доступ ко всем направлениям'
                    : selectedDirIds.size === allDirections.length
                      ? 'Доступ ко всем направлениям'
                      : `Ограничен: ${selectedDirIds.size} из ${allDirections.length}`}
                </p>
                <div className="grid grid-cols-2 gap-2">
                  {allDirections.map((dir) => (
                    <label
                      key={dir.id}
                      className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedDirIds.has(dir.id)}
                        onChange={(e) => {
                          const next = new Set(selectedDirIds)
                          if (e.target.checked) next.add(dir.id)
                          else next.delete(dir.id)
                          setSelectedDirIds(next)
                        }}
                        className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                      />
                      <span className="text-gray-700">{dir.name}</span>
                    </label>
                  ))}
                </div>
                {isEdit && (
                  <button
                    type="button"
                    onClick={async () => {
                      setDirLoading(true)
                      setError(null)
                      try {
                        const dirIds = Array.from(selectedDirIds)
                        const idsToSend = dirIds.length === allDirections.length ? [] : dirIds
                        await assignUserDirections(user!.id, idsToSend)
                      } catch (err: unknown) {
                        const message =
                          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
                          'Ошибка сохранения направлений'
                        setError(message)
                      } finally {
                        setDirLoading(false)
                      }
                    }}
                    disabled={dirLoading}
                    className="mt-3 rounded-lg bg-gray-100 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200 disabled:opacity-40"
                  >
                    {dirLoading ? 'Сохранение...' : 'Сохранить направления'}
                  </button>
                )}
              </div>
            )}
          </div>

          <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Отмена
            </button>
            <button
              type="submit"
              disabled={saving}
              className="rounded-lg bg-primary-500 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-600 disabled:opacity-50"
            >
              {saving ? 'Сохранение...' : isEdit ? 'Сохранить' : 'Создать'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
