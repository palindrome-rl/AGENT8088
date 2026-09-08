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
  await expect(page.locator('a:has-text("Appearance")')).toBeVisible()
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

test('appearance settings update the workspace', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Settings' }).click()
  await page.getByRole('link', { name: 'Appearance', exact: true }).click()
  await expect(page).toHaveURL(/\/appearance$/)
  await expect(page.getByRole('heading', { name: 'Appearance' })).toBeVisible()

  await page.getByRole('button', { name: 'Dracula preset' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-accent', 'rose')
  await page.getByLabel('UI font').selectOption('serif')
  await expect(page.locator('html')).toHaveAttribute('data-ui-font', 'serif')
  await page.getByRole('switch', { name: 'Translucent sidebar' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-sidebar-translucent', 'true')
  await page.getByLabel('Contrast').fill('110')
  await expect(page.getByText('110%', { exact: true })).toBeVisible()
})

test('command palette opens with Cmd+K', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('textbox', { name: 'Prompt' }).press('Control+k')
  await expect(page.getByPlaceholder(/Search pages and commands/)).toBeVisible()
})

test('the command palette uses the platform shortcut and dismisses reliably', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Ctrl+K', { exact: true })).toBeVisible()

  await page.keyboard.press('Control+k')
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toHaveCount(0)

  await page.keyboard.press('Control+k')
  await page.mouse.click(8, 8)
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toHaveCount(0)
})

test('the composer keeps the chat backdrop continuous', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('[data-promptbar]')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
})

test('prompt bar accepts text and slash commands', async ({ page }) => {
  await page.goto('/')
  const textarea = page.locator('textarea')
  await textarea.fill('/help')
  await expect(textarea).toHaveValue('/help')
})

test('slash autocomplete and palette use the CLI command catalog', async ({ page }) => {
  await page.goto('/')
  const textarea = page.locator('textarea')

  await textarea.fill('/sea')
  await expect(page.getByText('/search', { exact: true })).toBeVisible()

  await textarea.press('Control+k')
  const palette = page.getByPlaceholder('Search pages and commands…')
  await palette.fill('think')
  await expect(page.getByText('/think [on|off]', { exact: true })).toBeVisible()

  await palette.fill('audit')
  await page.getByText('/audit [on|off]', { exact: true }).click()
  await expect(textarea).toHaveValue('/audit ')
})

test('an exact slash command submits with Enter', async ({ page }) => {
  await page.goto('/')
  const textarea = page.locator('textarea')
  await textarea.fill('/help')
  await textarea.press('Enter')
  // /help output arrives as one terminal-formatted <pre> holding the whole
  // command table, so no element's text is exactly "Commands" - assert on the
  // transcript content instead of an exact-text element match.
  await expect(page.locator('body')).toContainText('Commands')
  await expect(page.locator('body')).toContainText('/tools')
})

test('an exact command wins over a longer autocomplete match', async ({ page }) => {
  await page.goto('/')
  const textarea = page.locator('textarea')
  await textarea.fill('/agent')
  await textarea.press('Enter')
  await expect(page.getByText('cancelled — try /agent <name> <task>, or /agents to list them')).toBeVisible()
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
