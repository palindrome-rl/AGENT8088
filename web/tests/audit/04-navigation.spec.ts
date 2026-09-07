import { test, expect } from '@playwright/test'

const ROUTES = ['/', '/artifacts', '/tools', '/skills', '/agents', '/mcp', '/memory',
  '/sessions', '/config', '/doctor', '/fusion', '/tasks', '/search', '/schedules']

test('deep link + hard refresh preserves each route (SPA fallback)', async ({ page }) => {
  const bad: string[] = []
  for (const r of ROUTES) {
    await page.goto(r, { waitUntil: 'networkidle' })
    await page.reload({ waitUntil: 'networkidle' })
    if (new URL(page.url()).pathname !== r) bad.push(`${r} -> ${new URL(page.url()).pathname}`)
    const txt = (await page.locator('body').innerText()).trim()
    if (txt.length < 5) bad.push(`${r} blank after refresh`)
  }
  expect(bad, bad.join('; ')).toHaveLength(0)
})

test('back button unwinds navigation history correctly', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' })
  const visited = ['/tools', '/skills', '/config']
  for (const r of visited) { await page.goto(r, { waitUntil: 'networkidle' }) }
  for (const expected of ['/skills', '/tools', '/']) {
    await page.goBack({ waitUntil: 'networkidle' })
    expect(new URL(page.url()).pathname, `back should land on ${expected}`).toBe(expected)
  }
})

test('unknown route does not white-screen', async ({ page }) => {
  const crashes: string[] = []
  page.on('pageerror', (e) => crashes.push(e.message))
  await page.goto('/definitely-not-a-route-xyz', { waitUntil: 'networkidle' })
  const body = (await page.locator('body').innerText()).trim()
  console.log(`unknown-route body length=${body.length}; text="${body.replace(/\s+/g,' ').slice(0,200)}"`)
  expect(crashes, crashes.join(';')).toHaveLength(0)
  // Documents whether a 404 view exists at all.
  expect(body.length, 'unknown route rendered an empty document').toBeGreaterThan(5)
})

test('status bar stays populated across navigation', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' })
  for (const r of ['/tools', '/config', '/']) {
    await page.goto(r, { waitUntil: 'networkidle' })
    const body = await page.locator('body').innerText()
    expect(body, `status bar missing on ${r}`).toMatch(/qwen3\.8-27b|ready|ctx/)
  }
})
