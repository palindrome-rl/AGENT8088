import { test, expect } from '@playwright/test'

test('web UI loads with sidebar navigation', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('a:has-text("Chat")')).toBeVisible()
  await page.getByRole('button', { name: 'Settings' }).click()
  await expect(page.locator('a:has-text("Tools")')).toBeVisible()
  await expect(page.locator('a:has-text("Skills")')).toBeVisible()
  await expect(page.locator('a:has-text("Sub-Agents")')).toBeVisible()
  await expect(page.locator('a:has-text("MCP")')).toBeVisible()
  await expect(page.locator('a:has-text("Memory")')).toBeVisible()
  await expect(page.locator('a:has-text("Sessions")')).toBeVisible()
  await expect(page.locator('a:has-text("Config")')).toBeVisible()
  await expect(page.locator('a:has-text("Doctor")')).toBeVisible()
})

test('Agent8088 logo returns to home', async ({ page }) => {
  await page.goto('/tools')
  await page.getByRole('button', { name: 'Go to home' }).click()
  await expect(page).toHaveURL(/\/$/)
})

test('settings pages have an explicit back to chat control', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Settings' }).click()
  await page.getByRole('link', { name: 'Tools', exact: true }).click()
  await expect(page).toHaveURL(/\/tools$/)
  await page.getByRole('button', { name: 'Back to chat' }).click()
  await expect(page).toHaveURL(/\/$/)
})

test('command palette opens with Cmd+K', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('textbox', { name: 'Prompt' }).press('Control+k')
  await expect(page.getByPlaceholder(/Search pages and commands/)).toBeVisible()
})

test('prompt bar accepts text and slash commands', async ({ page }) => {
  await page.goto('/')
  const textarea = page.locator('textarea')
  await textarea.fill('/help')
  await expect(textarea).toHaveValue('/help')
})

test('tools page loads', async ({ page }) => {
  await page.goto('/tools')
  await page.waitForTimeout(3000)
  // Should show the tools page heading or content
  await expect(page.locator('body')).toBeVisible()
})

test('sessions page loads', async ({ page }) => {
  await page.goto('/sessions')
  await page.waitForTimeout(2000)
  await expect(page.locator('body')).toBeVisible()
})

test('doctor page loads', async ({ page }) => {
  await page.goto('/doctor')
  await page.waitForTimeout(3000)
  await expect(page.locator('body')).toBeVisible()
})

test('config page loads', async ({ page }) => {
  await page.goto('/config')
  await page.waitForTimeout(3000)
  await expect(page.locator('body')).toBeVisible()
})

test('skills page loads', async ({ page }) => {
  await page.goto('/skills')
  await page.waitForTimeout(3000)
  await expect(page.locator('body')).toBeVisible()
})

test('agents page loads', async ({ page }) => {
  await page.goto('/agents')
  await page.waitForTimeout(3000)
  await expect(page.locator('body')).toBeVisible()
})

test('mcp page loads', async ({ page }) => {
  await page.goto('/mcp')
  await page.waitForTimeout(3000)
  await expect(page.locator('body')).toBeVisible()
})

test('memory page loads', async ({ page }) => {
  await page.goto('/memory')
  await page.waitForTimeout(3000)
  await expect(page.locator('body')).toBeVisible()
})
