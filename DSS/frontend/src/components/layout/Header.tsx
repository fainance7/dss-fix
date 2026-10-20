'use client'

import { useAuthStore } from '@/store/authStore'

interface HeaderProps {
  title: string
}

export default function Header({ title }: HeaderProps) {
  const { user } = useAuthStore()

  return (
    <header className="flex h-14 items-center justify-between border-b border-gray-200 bg-white px-6">
      <h1 className="text-base font-semibold text-gray-900">{title}</h1>
      {user && (
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded-full bg-primary-100 flex items-center justify-center text-primary-700 text-sm font-semibold">
            {user.username[0].toUpperCase()}
          </div>
          <span className="text-sm text-gray-700">{user.username}</span>
        </div>
      )}
    </header>
  )
}
