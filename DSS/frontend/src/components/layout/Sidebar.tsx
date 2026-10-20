'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import { useAuthStore } from '@/store/authStore'
import type { RoleName } from '@/types/auth'
import { clsx } from 'clsx'

interface NavItem {
  label: string
  href: string
  roles?: RoleName[]
  icon: string
  group?: string
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Дашборд',               href: '/dashboard',                roles: ['akim_obl', 'admin'],                              icon: '◉' },
  { label: 'Карта',                 href: '/dashboard/map',            roles: ['exp_dir', 'exp_ver', 'akim_ray', 'admin'],        icon: '◈' },
  { label: 'Карта направлений',     href: '/dashboard/direction-map',  roles: ['exp_dir', 'admin'],                                   icon: '◐' },
  { label: 'Аналитика',             href: '/dashboard/analytics',      roles: ['exp_dir', 'exp_ver', 'akim_obl', 'akim_ray', 'admin'], icon: '◆' },
  { label: 'Сценарии',              href: '/dashboard/scenarios',      roles: ['exp_dir', 'akim_obl', 'akim_ray', 'admin'],       icon: '◇' },
  { label: 'Ландшафтное планирование', href: '/dashboard/landscape',                                                               icon: '🌿' },
  { label: 'Мероприятия',           href: '/dashboard/tasks',          roles: ['akim_ray', 'akim_obl', 'admin'],                  icon: '◻' },
  { label: 'Каталог данных',        href: '/dashboard/data',           roles: ['exp_ver', 'exp_dir', 'admin'],                    icon: '▤' },
  { label: 'Управление данными',    href: '/dashboard/data/spatial',   roles: ['exp_ver', 'exp_dir', 'admin'],                    icon: '⬡' },
  { label: 'Импорт данных',         href: '/dashboard/data-intake',    roles: ['exp_ver', 'exp_dir', 'admin', 'data_uploader'],   icon: '⤓' },
  { label: 'Загрузка геоданных',    href: '/dashboard/data-intake/gis', roles: ['exp_ver', 'exp_dir', 'admin', 'data_uploader'],  icon: '🗺' },
  { label: 'Каталог геоданных',     href: '/dashboard/data-intake/gis/catalog', roles: ['exp_ver', 'exp_dir', 'admin', 'data_uploader'], icon: '🗂' },
  { label: 'Разметка remote-таблиц', href: '/dashboard/data-intake/remote', roles: ['exp_ver', 'admin', 'data_uploader'],            icon: '⛁' },
  { label: 'Импортированные значения', href: '/dashboard/indicator-values', roles: ['exp_ver', 'exp_dir', 'admin', 'data_uploader'], icon: '∑' },
  { label: 'Декады метеостанций',    href: '/dashboard/data-intake/station-dekads', roles: ['exp_ver', 'exp_dir', 'admin', 'data_uploader'], icon: '🌡' },
  { label: 'Справочники',            href: '/admin/references',         roles: ['admin'],                                          icon: '◫' },
  { label: 'Пользователи',          href: '/admin/users',              roles: ['admin'],                                          icon: '◎' },
  { label: 'Мониторинг',            href: '/admin/monitoring',         roles: ['admin'],                                          icon: '⚙' },
  { label: 'Журнал загрузок',       href: '/admin/monitoring',         roles: ['data_uploader'],                                  icon: '⚠' },
  { label: 'Настройки',             href: '/admin/settings',           roles: ['admin'],                                          icon: '◐' },
]

// Элемент в группе «Направления». Либо ссылка (href), либо узел со вложенными пунктами (children).
interface DirItem {
  label: string
  icon: string
  href?: string
  children?: { label: string; href: string }[]
}

// Тематические дашборды по 10 направлениям. Видны экспертам направлений и руководству.
const DIRECTION_ITEMS: DirItem[] = [
  { label: 'Экономика',           href: '/dashboard/economy',     icon: '₸' },
  { label: 'Сельское хозяйство',  href: '/dashboard/agriculture', icon: '🌾' },
  { label: 'Земельные ресурсы',   href: '/dashboard/land',        icon: '🌍' },
  {
    label: 'Социал-демография',
    icon: '👥',
    children: [
      { label: 'Демографическое развитие региона',          href: '/dashboard/demography' },
      { label: 'Социальная инфраструктура и качество жизни', href: '/dashboard/social-infrastructure' },
      { label: 'Демографические риски рынка труда',          href: '/dashboard/demographic-risk' },
      { label: 'Социальная устойчивость',                    href: '/dashboard/social-sustainability' },
    ],
  },
  { label: 'Трудовые ресурсы',    href: '/dashboard/labour',      icon: '🛠' },
  {
    label: 'Климат',
    icon: '🌡',
    children: [
      { label: 'Термические ресурсы', href: '/dashboard/climate/thermal' },
      { label: 'Атмосферные осадки', href: '/dashboard/climate/precipitation' },
      { label: 'Влагообеспеченность вегетационного периода (K(5-8))', href: '/dashboard/climate/moisture' },
      { label: 'Засушливость вегетационного периода (ГТК(5-8))', href: '/dashboard/climate/aridity' },
      { label: 'Обзор', href: '/dashboard/climate' },
    ],
  },
  { label: 'Экология',            href: '/dashboard/ecology',     icon: '🌳' },
  { label: 'Туризм',              href: '/dashboard/tourism',     icon: '🏞' },
  { label: 'Транспорт',           href: '/dashboard/transport',   icon: '🛣' },
  {
    label: 'Водные ресурсы',
    icon: '💧',
    children: [
      { label: 'Обзор', href: '/dashboard/water' },
      { label: 'Водохозяйственный фонд', href: '/dashboard/water/fund' },
      { label: 'Водообеспеченность', href: '/dashboard/water/supply' },
      { label: 'Качество и баланс', href: '/dashboard/water/quality' },
    ],
  },
]

function flattenDirHrefs(items: DirItem[]): string[] {
  const out: string[] = []
  for (const it of items) {
    if (it.href) out.push(it.href)
    if (it.children) for (const c of it.children) out.push(c.href)
  }
  return out
}

const DIRECTION_NAV_ROLES: RoleName[] = ['exp_dir', 'exp_ver', 'akim_obl', 'akim_ray', 'admin']

export default function Sidebar() {
  const pathname = usePathname()
  const { user, hasRole, logout } = useAuthStore()

  // Зависимость — сам user, а не hasRole (zustand-функция стабильна и не триггерит memo).
  const visibleItems = useMemo(
    () =>
      NAV_ITEMS.filter((item) => !item.roles || item.roles.some((r) => hasRole(r)))
        // один href — один пункт (admin+data_uploader видит «Мониторинг», а не оба)
        .filter((item, i, arr) => arr.findIndex((x) => x.href === item.href) === i),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [user],
  )
  const showDirections = DIRECTION_NAV_ROLES.some((r) => hasRole(r))

  // Подсветка только одного "лучшего" пункта (самый длинный href, под который подходит pathname).
  // Раньше /dashboard/data-intake/remote подсвечивал и /dashboard/data-intake.
  const activeHref = useMemo(() => {
    const allHrefs: string[] = [
      ...visibleItems.map((i) => i.href),
      ...flattenDirHrefs(DIRECTION_ITEMS),
    ]
    const matches = allHrefs
      .filter((h) => pathname === h || pathname.startsWith(h + '/'))
      .sort((a, b) => b.length - a.length)
    return matches[0] ?? null
  }, [visibleItems, pathname])

  // Раскрытие группы «Направления» — авто-открывается, если текущий путь — направление.
  const directionMatches = flattenDirHrefs(DIRECTION_ITEMS).some(
    (h) => pathname === h || pathname.startsWith(h + '/'),
  )
  const [directionsOpen, setDirectionsOpen] = useState<boolean>(directionMatches)
  useEffect(() => {
    if (directionMatches) setDirectionsOpen(true)
  }, [directionMatches])

  // Состояние раскрытия для узлов с children (например, «Социал-демография»).
  // Автораскрытие, если pathname попадает в один из дочерних.
  const [openNodes, setOpenNodes] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {}
    for (const it of DIRECTION_ITEMS) {
      if (it.children) {
        init[it.label] = it.children.some(
          (c) => pathname === c.href || pathname.startsWith(c.href + '/'),
        )
      }
    }
    return init
  })
  useEffect(() => {
    setOpenNodes((prev) => {
      const next = { ...prev }
      for (const it of DIRECTION_ITEMS) {
        if (it.children) {
          const inChild = it.children.some(
            (c) => pathname === c.href || pathname.startsWith(c.href + '/'),
          )
          if (inChild) next[it.label] = true
        }
      }
      return next
    })
  }, [pathname])

  return (
    <aside className="flex h-full w-60 flex-col bg-gray-900 text-gray-100">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-gray-700">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-500 text-white font-bold text-sm">
          Д
        </div>
        <div>
          <div className="text-sm font-semibold">ДСС</div>
          <div className="text-xs text-gray-400">СКО</div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
        {visibleItems.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={clsx(
              'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
              activeHref === item.href
                ? 'bg-primary-600 text-white'
                : 'text-gray-300 hover:bg-gray-800 hover:text-white',
            )}
          >
            <span className="text-base">{item.icon}</span>
            {item.label}
          </Link>
        ))}

        {showDirections && (
          <div className="mt-4">
            <button
              type="button"
              onClick={() => setDirectionsOpen((v) => !v)}
              className={clsx(
                'flex w-full items-center justify-between rounded-lg px-3 py-2 text-xs font-semibold uppercase tracking-wider transition-colors',
                directionMatches ? 'text-white' : 'text-gray-400 hover:text-gray-200',
              )}
            >
              <span className="flex items-center gap-2">
                <span className="text-base">▦</span>
                Направления
              </span>
              <span className="text-gray-500">{directionsOpen ? '▾' : '▸'}</span>
            </button>
            {directionsOpen && (
              <div className="mt-1 space-y-1 pl-2">
                {DIRECTION_ITEMS.map((item) => {
                  if (item.children) {
                    const isOpen = !!openNodes[item.label]
                    const childActive = item.children.some(
                      (c) => activeHref === c.href,
                    )
                    return (
                      <div key={item.label}>
                        <button
                          type="button"
                          onClick={() =>
                            setOpenNodes((prev) => ({
                              ...prev,
                              [item.label]: !prev[item.label],
                            }))
                          }
                          className={clsx(
                            'flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                            childActive
                              ? 'text-white'
                              : 'text-gray-300 hover:bg-gray-800 hover:text-white',
                          )}
                        >
                          <span className="text-base">{item.icon}</span>
                          <span className="flex-1 text-left">{item.label}</span>
                          <span className="text-gray-500 text-xs">
                            {isOpen ? '▾' : '▸'}
                          </span>
                        </button>
                        {isOpen && (
                          <div className="mt-1 space-y-1 pl-7">
                            {item.children.map((c) => (
                              <Link
                                key={c.href}
                                href={c.href}
                                className={clsx(
                                  'block rounded-lg px-3 py-1.5 text-sm transition-colors',
                                  activeHref === c.href
                                    ? 'bg-primary-600 text-white'
                                    : 'text-gray-400 hover:bg-gray-800 hover:text-white',
                                )}
                              >
                                {c.label}
                              </Link>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  }
                  return (
                    <Link
                      key={item.href}
                      href={item.href!}
                      className={clsx(
                        'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                        activeHref === item.href
                          ? 'bg-primary-600 text-white'
                          : 'text-gray-300 hover:bg-gray-800 hover:text-white',
                      )}
                    >
                      <span className="text-base">{item.icon}</span>
                      {item.label}
                    </Link>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </nav>

      {/* User info + logout */}
      {user && (
        <div className="border-t border-gray-700 px-4 py-4">
          <div className="mb-1 text-xs text-gray-400">
            {user.roles.map((r) => r.name).join(', ')}
          </div>
          <Link
            href="/dashboard/profile"
            className="text-sm font-medium truncate block hover:text-primary-300 transition-colors"
          >
            {user.username}
          </Link>
          <div className="mt-3 flex gap-2">
            <Link
              href="/dashboard/profile"
              className="flex-1 rounded-lg bg-gray-800 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700 hover:text-white transition-colors text-center"
            >
              Профиль
            </Link>
            <button
              onClick={logout}
              className="flex-1 rounded-lg bg-gray-800 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700 hover:text-white transition-colors"
            >
              Выйти
            </button>
          </div>
        </div>
      )}
    </aside>
  )
}
