import { test, expect } from '@playwright/test'
import { USERS, login, logout } from './helpers'

test.describe('Admin panel (admin)', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, USERS.admin.username, USERS.admin.password)
  })

  test('/admin загружается — заголовок виден', async ({ page }) => {
    await page.goto('/admin/monitoring')
    // Wait for hydration — page shows either heading or forbidden message
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 10_000 })
    // Admin should NOT see forbidden message
    await expect(page.locator('text=Доступ запрещён')).not.toBeVisible()
  })

  test('/admin показывает системные метрики или статус сервисов', async ({ page }) => {
    await page.goto('/admin/monitoring')
    // Either metrics loaded from API or static service list is shown
    await expect(
      page.locator('text=/PostgreSQL|Backend|Слои|Записи|Источники/i').first()
    ).toBeVisible({ timeout: 12_000 })
  })

  test('/admin/users отображает таблицу пользователей', async ({ page }) => {
    await page.goto('/admin/users')
    await expect(page.locator('table, [role="table"], tbody').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByRole('cell', { name: 'admin' }).first()).toBeVisible()
  })

  test('sidebar Мониторинг активен на /admin', async ({ page }) => {
    await page.goto('/admin/monitoring')
    await expect(page.locator('nav')).toBeVisible({ timeout: 8_000 })
    // Мониторинг link should have active class (bg-primary-600)
    const monitoringLink = page.locator('nav a').filter({ hasText: 'Мониторинг' })
    await expect(monitoringLink).toHaveClass(/bg-primary/, { timeout: 5_000 })
  })

  test('sidebar logout works from /admin', async ({ page }) => {
    await page.goto('/admin/monitoring')
    await logout(page)
    await expect(page).toHaveURL(/\/login/)
  })
})

test.describe('Dashboard KPI (akim_obl)', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, USERS.akim_obl.username, USERS.akim_obl.password)
  })

  test('/dashboard загружается с KPI карточками', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.locator('text=/ВРП|Население|Занятость|Инвестиции/i').first()).toBeVisible({ timeout: 10_000 })
  })

  test('KPI из реальных данных: у карточек год источника, нет выдуманной периодичности', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.locator('text=/^20\\d\\d г\\.$/').first()).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('button', { name: 'Квартал' })).toHaveCount(0)
  })

  test('sidebar: активен Дашборд', async ({ page }) => {
    await page.goto('/dashboard')
    const link = page.locator('nav a').filter({ hasText: 'Дашборд' })
    await expect(link).toHaveClass(/bg-primary/)
  })
})

test.describe('Map (exp_dir)', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, USERS.exp_dir.username, USERS.exp_dir.password)
  })

  test('/dashboard/map содержит canvas (MapLibre)', async ({ page }) => {
    await page.goto('/dashboard/map')
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 15_000 })
  })

  test('панель слоёв отображается', async ({ page }) => {
    await page.goto('/dashboard/map')
    await expect(page.locator('text=/Слои|Layer/i').first()).toBeVisible({ timeout: 10_000 })
  })
})

test.describe('Scenarios (akim_obl)', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, USERS.akim_obl.username, USERS.akim_obl.password)
  })

  test('/dashboard/scenarios загружается', async ({ page }) => {
    await page.goto('/dashboard/scenarios')
    // Page renders — shows either scenario cards or empty/loading state
    await expect(page.locator('h1, h2, h3').first()).toBeVisible({ timeout: 10_000 })
    // Should not show access error
    await expect(page.locator('text=Доступ запрещён')).not.toBeVisible()
  })
})

test.describe('Tasks (akim_ray)', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, USERS.akim_ray.username, USERS.akim_ray.password)
  })

  test('/dashboard/tasks загружается со статус-карточками', async ({ page }) => {
    await page.goto('/dashboard/tasks')
    await expect(page.locator('text=/Мероприяти/').first()).toBeVisible({ timeout: 10_000 })
  })
})

test.describe('Data catalog (exp_ver)', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, USERS.exp_ver.username, USERS.exp_ver.password)
  })

  test('/dashboard/data показывает каталог слоёв', async ({ page }) => {
    await page.goto('/dashboard/data')
    await expect(page.locator('text=/Каталог|Слой|Layer/i').first()).toBeVisible({ timeout: 10_000 })
  })
})

test.describe('Analytics (exp_dir)', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, USERS.exp_dir.username, USERS.exp_dir.password)
  })

  test('/dashboard/analytics загружается', async ({ page }) => {
    await page.goto('/dashboard/analytics')
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 10_000 })
  })

  test('аналитика по реальным районам (Есильский — не было в прежнем захардкоженном списке)', async ({ page }) => {
    await page.goto('/dashboard/analytics')
    await expect(page.locator('text=Есильский район').first()).toBeVisible({ timeout: 20_000 })
  })
})

// admin: у exp_dir могут быть назначены не все направления — тогда данных экономики не будет (так и должно быть).
test.describe('Direction map (admin)', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, USERS.admin.username, USERS.admin.password)
  })

  test('картограмма экономики: легенда с реальной единицей и годом', async ({ page }) => {
    await page.goto('/dashboard/direction-map?direction=economy')
    await expect(page.locator('text=/Легенда — .*, 20\\d\\d/').first()).toBeVisible({ timeout: 25_000 })
    await expect(page.locator('text=/млрд\\.?\\s?тенге/').first()).toBeVisible()
  })
})
