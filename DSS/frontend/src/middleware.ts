import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
import { SESSION_MARK } from '@/lib/session'

const PUBLIC_PATHS = ['/login', '/api', '/geo']

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl
  const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p))

  if (isPublic) return NextResponse.next()

  // Признак входа (без секрета): сами токены — в httpOnly-cookie API, сюда не приходят.
  if (!request.cookies.get(SESSION_MARK)?.value) {
    const loginUrl = new URL('/login', request.url)
    loginUrl.searchParams.set('redirect', pathname)
    return NextResponse.redirect(loginUrl)
  }

  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}
