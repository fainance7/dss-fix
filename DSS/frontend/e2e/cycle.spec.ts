/**
 * TC-CYCLE-001: Полный цикл — Генерация сценариев → Утверждение → Задачи
 *
 * Шаги:
 * 1. exp_dir генерирует сценарии на странице /dashboard/scenarios
 * 2. akim_obl утверждает оптимальный сценарий
 * 3. akim_ray видит задачи на /dashboard/tasks
 */

import { test, expect } from '@playwright/test'
import { USERS, login } from './helpers'

test.describe('TC-CYCLE-001: Полный цикл сценарий → задачи', () => {
  test('exp_dir видит страницу сценариев', async ({ page }) => {
    await login(page, USERS.exp_dir.username, USERS.exp_dir.password)
    await page.goto('/dashboard/scenarios')
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('text=Доступ запрещён')).not.toBeVisible()
  })

  test('akim_obl видит страницу сценариев', async ({ page }) => {
    await login(page, USERS.akim_obl.username, USERS.akim_obl.password)
    await page.goto('/dashboard/scenarios')
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('text=Доступ запрещён')).not.toBeVisible()
  })

  test('akim_ray видит страницу задач', async ({ page }) => {
    await login(page, USERS.akim_ray.username, USERS.akim_ray.password)
    await page.goto('/dashboard/tasks')
    await expect(page.locator('text=/задач|Задач/i').first()).toBeVisible({ timeout: 10_000 })
  })
})

test.describe('TC-CYCLE-002: API полный цикл через fetch', () => {
  /**
   * Тест через browser fetch() — проверяет что frontend может вызвать
   * все API endpoints полного цикла через Next.js proxy.
   */
  test('frontend proxy /ai/api/v1/scenarios доступен', async ({ page }) => {
    await login(page, USERS.exp_dir.username, USERS.exp_dir.password)

    // Test AI proxy is accessible from frontend
    const result = await page.evaluate(async () => {
      const token = localStorage.getItem('access_token')
      const res = await fetch('/ai/health')
      return { status: res.status, ok: res.ok }
    })
    expect(result.status).toBe(200)
  })

  test('exp_dir может получить список сценариев через API', async ({ page }) => {
    await login(page, USERS.exp_dir.username, USERS.exp_dir.password)

    const scenarios = await page.evaluate(async () => {
      const token = localStorage.getItem('access_token')
      const res = await fetch('/ai/api/v1/scenarios', {
        headers: {
          'Authorization': `Bearer ${token}`,
          'X-AI-Key': 'dss_ai_service_key_2026',
        },
      })
      return res.json()
    })
    expect(Array.isArray(scenarios)).toBe(true)
  })

  test('akim_obl может получить список сценариев через API', async ({ page }) => {
    await login(page, USERS.akim_obl.username, USERS.akim_obl.password)

    const scenarios = await page.evaluate(async () => {
      const token = localStorage.getItem('access_token')
      const res = await fetch('/ai/api/v1/scenarios', {
        headers: {
          'Authorization': `Bearer ${token}`,
          'X-AI-Key': 'dss_ai_service_key_2026',
        },
      })
      return res.json()
    })
    expect(Array.isArray(scenarios)).toBe(true)
    // Should see approved scenarios (at least 1 from setup)
    const approved = scenarios.filter((s: { status: string }) => s.status === 'approved')
    expect(approved.length).toBeGreaterThanOrEqual(1)
  })

  test('akim_ray видит задачи через API', async ({ page }) => {
    await login(page, USERS.akim_ray.username, USERS.akim_ray.password)

    const tasks = await page.evaluate(async () => {
      const token = localStorage.getItem('access_token')
      const res = await fetch('/ai/api/v1/tasks', {
        headers: {
          'Authorization': `Bearer ${token}`,
          'X-AI-Key': 'dss_ai_service_key_2026',
        },
      })
      return res.json()
    })
    const taskList = Array.isArray(tasks) ? tasks : (tasks.items ?? [])
    // Tasks were created when scenario was approved
    expect(taskList.length).toBeGreaterThanOrEqual(1)
  })

  test('полный цикл: generate → approve → tasks (API)', async ({ page }) => {
    // Step 1: Login as exp_dir and generate scenarios
    await login(page, USERS.exp_dir.username, USERS.exp_dir.password)

    const generated = await page.evaluate(async () => {
      const token = localStorage.getItem('access_token')

      // Get user ID
      const meRes = await fetch('/api/v1/auth/me', {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      const me = await meRes.json()

      // Get region
      const regRes = await fetch('/api/v1/regions', {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      const regions = await regRes.json()
      const regionId = regions[0].id

      // Generate scenarios
      const genRes = await fetch('/ai/api/v1/scenarios/generate', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'X-AI-Key': 'dss_ai_service_key_2026',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          region_id: regionId,
          target_year: 2028,
          created_by: me.id,
        }),
      })
      const scenarios = await genRes.json()
      return { scenarios, status: genRes.status }
    })

    expect(generated.status).toBe(201)
    expect(Array.isArray(generated.scenarios)).toBe(true)
    expect(generated.scenarios).toHaveLength(3)

    const optimalId = generated.scenarios.find(
      (s: { scenario_type: string }) => s.scenario_type === 'optimal'
    ).id

    // Step 2: Login as akim_obl and approve
    await page.goto('/login')
    await login(page, USERS.akim_obl.username, USERS.akim_obl.password)

    const approved = await page.evaluate(async (scenarioId: string) => {
      const token = localStorage.getItem('access_token')
      const meRes = await fetch('/api/v1/auth/me', {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      const me = await meRes.json()

      const approveRes = await fetch('/ai/api/v1/internal/scenario-approve', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'X-AI-Key': 'dss_ai_service_key_2026',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          scenario_id: scenarioId,
          approved_by: me.id,
          comment: 'E2E test approval',
        }),
      })
      const result = await approveRes.json()
      return { status: approveRes.status, result }
    }, optimalId)

    expect(approved.status).toBe(200)
    expect(approved.result.status).toBe('approved')
    expect(approved.result.tasks_count).toBeGreaterThanOrEqual(1)

    // Step 3: Login as akim_ray and check tasks
    await page.goto('/login')
    await login(page, USERS.akim_ray.username, USERS.akim_ray.password)

    const tasks = await page.evaluate(async (scenarioId: string) => {
      const token = localStorage.getItem('access_token')
      const res = await fetch(`/ai/api/v1/tasks?scenario_id=${scenarioId}`, {
        headers: {
          'Authorization': `Bearer ${token}`,
          'X-AI-Key': 'dss_ai_service_key_2026',
        },
      })
      return res.json()
    }, optimalId)

    const taskList = Array.isArray(tasks) ? tasks : (tasks.items ?? [])
    expect(taskList.length).toBeGreaterThanOrEqual(1)
    expect(taskList[0].status).toBe('pending')
  })
})
