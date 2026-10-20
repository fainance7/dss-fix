import { test, expect } from '@playwright/test'
import { USERS, login } from './helpers'

// Nav links contain icon + label text, e.g. "◉Дашборд"
// Match by checking if any label *contains* the expected string
async function navContains(page: import('@playwright/test').Page, label: string) {
  const labels = await page.locator('nav a').allTextContents()
  return labels.some((t) => t.includes(label))
}

test.describe('TC-RBAC: Навигация по ролям', () => {
  test('admin видит все пункты меню', async ({ page }) => {
    await login(page, USERS.admin.username, USERS.admin.password)
    for (const label of ['Дашборд', 'Карта', 'Аналитика', 'Сценарии', 'Мероприятия', 'Каталог данных', 'Пользователи', 'Мониторинг']) {
      expect(await navContains(page, label), `Ожидается пункт "${label}"`).toBe(true)
    }
  })

  test('exp_dir видит: Карта, Аналитика, Сценарии, Каталог данных', async ({ page }) => {
    await login(page, USERS.exp_dir.username, USERS.exp_dir.password)
    for (const label of ['Карта', 'Аналитика', 'Сценарии', 'Каталог данных']) {
      expect(await navContains(page, label), `Ожидается пункт "${label}"`).toBe(true)
    }
    for (const label of ['Дашборд', 'Мероприятия', 'Пользователи', 'Мониторинг']) {
      expect(await navContains(page, label), `Не ожидается пункт "${label}"`).toBe(false)
    }
  })

  test('exp_ver видит: Карта, Каталог данных', async ({ page }) => {
    await login(page, USERS.exp_ver.username, USERS.exp_ver.password)
    for (const label of ['Карта', 'Каталог данных']) {
      expect(await navContains(page, label), `Ожидается пункт "${label}"`).toBe(true)
    }
    for (const label of ['Дашборд', 'Сценарии', 'Мероприятия']) {
      expect(await navContains(page, label), `Не ожидается пункт "${label}"`).toBe(false)
    }
  })

  test('akim_obl видит: Дашборд, Аналитика, Сценарии, Мероприятия', async ({ page }) => {
    await login(page, USERS.akim_obl.username, USERS.akim_obl.password)
    for (const label of ['Дашборд', 'Аналитика', 'Сценарии', 'Мероприятия']) {
      expect(await navContains(page, label), `Ожидается пункт "${label}"`).toBe(true)
    }
    for (const label of ['Карта', 'Пользователи', 'Мониторинг']) {
      expect(await navContains(page, label), `Не ожидается пункт "${label}"`).toBe(false)
    }
  })

  test('akim_ray видит Мероприятия, но не Дашборд области и администрирование', async ({ page }) => {
    await login(page, USERS.akim_ray.username, USERS.akim_ray.password)
    expect(await navContains(page, 'Мероприятия')).toBe(true)
    for (const label of ['Дашборд', 'Пользователи', 'Мониторинг']) {
      expect(await navContains(page, label), `Не ожидается пункт "${label}"`).toBe(false)
    }
  })
})

test.describe('TC-RBAC: Защита маршрутов', () => {
  test('неавторизованный → /dashboard → редирект на /login', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page).toHaveURL(/\/login/)
  })

  test('неавторизованный → /admin → редирект на /login', async ({ page }) => {
    await page.goto('/admin/monitoring')
    await expect(page).toHaveURL(/\/login/)
  })

  test('akim_ray → /admin → видит "Доступ запрещён"', async ({ page }) => {
    await login(page, USERS.akim_ray.username, USERS.akim_ray.password)
    await page.goto('/admin/monitoring')
    // Page loads but shows access denied message (client-side RBAC)
    await expect(page.locator('text=Доступ запрещён')).toBeVisible({ timeout: 8_000 })
  })

  test('akim_ray → /admin/users → редирект', async ({ page }) => {
    await login(page, USERS.akim_ray.username, USERS.akim_ray.password)
    await page.goto('/admin/users')
    await expect(page).toHaveURL(/\/(login|dashboard)/)
  })

  test('/dev-preview удалена и без входа уводит на /login', async ({ page }) => {
    await page.goto('/dev-preview?target=/admin/users')
    await expect(page).toHaveURL(/\/login/)
  })

  test('exp_dir не может утвердить сценарий прямым запросом к API (403)', async ({ page }) => {
    await login(page, USERS.exp_dir.username, USERS.exp_dir.password)
    const status = await page.evaluate(async () => {
      const r = await fetch('/api/v1/ai/api/v1/internal/scenario-approve', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('access_token')}`,
        },
        body: JSON.stringify({ scenario_id: '00000000-0000-0000-0000-000000000000', comment: 'e2e' }),
      })
      return r.status
    })
    expect(status).toBe(403)
  })
})
