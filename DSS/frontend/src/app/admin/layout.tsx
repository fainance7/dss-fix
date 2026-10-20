'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import Sidebar from '@/components/layout/Sidebar'
import { useAuthStore } from '@/store/authStore'

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const { isAuthenticated, fetchMe, user, _hasHydrated } = useAuthStore()

  useEffect(() => {
    if (!_hasHydrated) return
    if (!isAuthenticated) {
      router.replace('/login')
      return
    }
    if (!user) {
      fetchMe()
    }
  }, [_hasHydrated, isAuthenticated, user, fetchMe, router])

  if (!_hasHydrated) return null
  if (!isAuthenticated) return null

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <main className="flex-1 overflow-y-auto bg-gray-50 p-6">
          {children}
        </main>
      </div>
    </div>
  )
}
