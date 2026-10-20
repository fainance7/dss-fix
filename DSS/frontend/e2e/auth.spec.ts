import { test, expect } from '@playwright/test'
import { USERS, login, logout } from './helpers'

test.describe('TC-AUTH-001: Успешный вход по роли', () => {
  test('admin → /admin/monitoring', async ({ page }) => {
    await login(page, USERS.admin.username, USERS.admin.password)
    await expect(page).toHaveURL(/\/admin/)
  })

  test('exp_dir → /dashboard/map', async ({ page }) => {
    await login(page, USERS.exp_dir.username, USERS.exp_dir.password)
    await expect(page).toHaveURL(/\/dashboard\/map/)
  })

  test('exp_ver → /dashboard/map', async ({ page }) => {
    await login(page, USERS.exp_ver.username, USERS.exp_ver.password)
    await expect(page).toHaveURL(/\/dashboard\/map/)
  })

  test('akim_obl → /dashboard', async ({ page }) => {
    await login(page, USERS.akim_obl.username, USERS.akim_obl.password)
    await expect(page).toHaveURL(/\/dashboard$/)
  })

  test('akim_ray → /dashboard/tasks', async ({ page }) => {
    await login(page, USERS.akim_ray.username, USERS.akim_ray.password)
    await expect(page).toHaveURL(/\/dashboard\/tasks/)
  })
})

test.describe('TC-AUTH-002: Неверный пароль → показывает ошибку', () => {
  test('wrong password shows error message', async ({ page }) => {
    await page.goto('/login')
    await page.fill('#username', 'admin')
    await page.fill('#password', 'wrongpassword')
    await page.click('button[type="submit"]')
    // Error block should appear (authStore.error is set)
    await expect(page.locator('.bg-red-50')).toBeVisible({ timeout: 8_000 })
    // Should stay on login page
    await expect(page).toHaveURL(/\/login/)
  })
})

test.describe('Logout', () => {
  test('admin logout redirects to /login', async ({ page }) => {
    await login(page, USERS.admin.username, USERS.admin.password)
    await logout(page)
    await expect(page).toHaveURL(/\/login/)
  })

  test('after logout /dashboard redirects to /login', async ({ page }) => {
    await login(page, USERS.akim_obl.username, USERS.akim_obl.password)
    await logout(page)
    await page.goto('/dashboard')
    await expect(page).toHaveURL(/\/login/)
  })
})

test.describe('Login page', () => {
  test('shows username and password fields', async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('#username')).toBeVisible()
    await expect(page.locator('#password')).toBeVisible()
    await expect(page.locator('button[type="submit"]')).toBeVisible()
  })

  test('redirect param preserved after login', async ({ page }) => {
    await page.goto('/login?redirect=/dashboard/scenarios')
    await page.fill('#username', USERS.akim_obl.username)
    await page.fill('#password', USERS.akim_obl.password)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/dashboard\/scenarios/)
  })
})
