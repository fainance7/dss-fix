'use client'

import { UserOut } from '@/types/user'
import { clsx } from 'clsx'

interface UserTableProps {
  users: UserOut[]
  onEdit: (user: UserOut) => void
  onDelete: (user: UserOut) => void
  onResetPassword: (user: UserOut) => void
  isLoading?: boolean
}

export default function UserTable({ users, onEdit, onDelete, onResetPassword, isLoading }: UserTableProps) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-gray-400 text-sm">
        Загрузка...
      </div>
    )
  }

  if (!users.length) {
    return (
      <div className="flex items-center justify-center py-12 text-gray-400 text-sm">
        Пользователи не найдены
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-left">
            <th className="pb-3 pr-4 font-medium text-gray-500">Пользователь</th>
            <th className="pb-3 pr-4 font-medium text-gray-500">Email</th>
            <th className="pb-3 pr-4 font-medium text-gray-500">ИИН</th>
            <th className="pb-3 pr-4 font-medium text-gray-500">Роли</th>
            <th className="pb-3 pr-4 font-medium text-gray-500">Статус</th>
            <th className="pb-3 font-medium text-gray-500">Действия</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {users.map((user) => (
            <tr key={user.id} className="hover:bg-gray-50">
              <td className="py-3 pr-4 font-medium text-gray-900">{user.username}</td>
              <td className="py-3 pr-4 text-gray-500">{user.email}</td>
              <td className="py-3 pr-4 font-mono text-xs text-gray-500">
                {user.iin ?? <span className="text-gray-300">—</span>}
              </td>
              <td className="py-3 pr-4">
                <div className="flex flex-wrap gap-1">
                  {user.roles.length === 0 ? (
                    <span className="text-gray-400 text-xs">нет ролей</span>
                  ) : (
                    user.roles.map((r, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center rounded-md bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-700 ring-1 ring-primary-200"
                      >
                        {r.role_name}
                      </span>
                    ))
                  )}
                </div>
              </td>
              <td className="py-3 pr-4">
                <span
                  className={clsx(
                    'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
                    user.is_active
                      ? 'bg-green-50 text-green-700 ring-1 ring-green-200'
                      : 'bg-red-50 text-red-700 ring-1 ring-red-200',
                  )}
                >
                  {user.is_active ? 'Активен' : 'Заблокирован'}
                </span>
              </td>
              <td className="py-3">
                <div className="flex gap-2">
                  <button
                    onClick={() => onEdit(user)}
                    className="rounded-md bg-gray-100 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-200 transition-colors"
                  >
                    Изменить
                  </button>
                  <button
                    onClick={() => onResetPassword(user)}
                    className="rounded-md bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100 transition-colors"
                  >
                    Сброс пароля
                  </button>
                  <button
                    onClick={() => onDelete(user)}
                    className="rounded-md bg-red-50 px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-100 transition-colors"
                  >
                    Удалить
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
