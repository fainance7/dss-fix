import { Suspense } from 'react'
import LoginForm from '@/components/auth/LoginForm'

export default function LoginPage() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-50 to-indigo-100 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Logo / title */}
        <div className="mb-8 text-center">
          <div className="inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-primary-500 shadow-lg mb-4">
            <span className="text-2xl font-bold text-white">Д</span>
          </div>
          <h1 className="text-2xl font-bold text-gray-900">ДСС</h1>
          <p className="mt-1 text-sm text-gray-500">
            Система поддержки принятия решений
          </p>
          <p className="text-xs text-gray-400 mt-0.5">Северо-Казахстанская область</p>
        </div>

        {/* Card */}
        <div className="rounded-2xl bg-white p-8 shadow-xl ring-1 ring-gray-100">
          <h2 className="text-lg font-semibold text-gray-900 mb-6">Вход в систему</h2>
          <Suspense>
            <LoginForm />
          </Suspense>
        </div>

        <p className="mt-6 text-center text-xs text-gray-400">
          © 2026 АО «ДСС». Все права защищены.
        </p>
      </div>
    </div>
  )
}
