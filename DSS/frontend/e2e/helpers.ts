import { Page } from '@playwright/test'

export const USERS = {
  admin:    { username: 'admin',      password: 'CHANGE_ME' },
  exp_ver:  { username: 'exp_ver',    password: 'Test2026' },
  exp_dir:  { username: 'exp_dir',    password: 'Test2026' },
  akim_obl: { username: 'akim_obl',   password: 'Test2026' },
  akim_ray: { username: 'akim_petro', password: 'Test2026' },
} as const

export async function login(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.fill('#username', username)
  await page.fill('#password', password)
  await page.click('button[type="submit"]')
  await page.waitForURL(/\/(dashboard|admin)/, { timeout: 10_000 })
}

export async function logout(page: Page) {
  await page.click('button:has-text("Выйти")')
  await page.waitForURL('**/login')
}
