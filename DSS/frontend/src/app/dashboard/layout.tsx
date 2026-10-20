'use client'

import { useEffect } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import Sidebar from '@/components/layout/Sidebar'
import { useAuthStore } from '@/store/authStore'

// Pages that need full-bleed layout (no padding, no scroll)
const FULL_BLEED_PATHS = ['/dashboard/map']

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const { user, isAuthenticated, fetchMe, _hasHydrated } = useAuthStore()

  useEffect(() => {
    if (!_hasHydrated) return       // wait for localStorage restore
    if (!isAuthenticated) {
      router.replace('/login')
      return
    }
    if (!user) {
      fetchMe()
    }
  }, [_hasHydrated, isAuthenticated, user, fetchMe, router])

  // Don't render until Zustand has read from localStorage
  if (!_hasHydrated) return null
  if (!isAuthenticated) return null

  const isFullBleed = FULL_BLEED_PATHS.includes(pathname)

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <main
          className={
            isFullBleed
              ? 'relative overflow-hidden'
              : 'flex-1 overflow-y-auto bg-gray-50 p-6'
          }
          style={isFullBleed ? { flex: '1 1 0', height: 0, minHeight: 0 } : undefined}
        >
          {children}
        </main>
      </div>
    </div>
  )
}
