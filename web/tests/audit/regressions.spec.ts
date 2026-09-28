/**
 * Regression guards for the defects found in the end-to-end Web UI audit of
 * origin/development (2026-09-07). Each one asserts the fixed behaviour.
 *
 * Needs a running backend; point AUDIT_BASE at it (default 127.0.0.1:8280).
 * Run: npx playwright test -c playwright.audit.config.ts tests/audit/regressions.spec.ts
 */
import { test, expect } from '@playwright/test'

const composer = (page: any) => page.locator('textarea').first()

/* ── W-01 (High): chat transcript is lost on refresh ───────────────────────
 * /api/history holds the messages and the session file is on disk, but
 * useWebSocket calls syncSession() without includeHistory on mount, so the
 * transcript is never restored. */
test('W-01 chat transcript survives a hard refresh', async ({ page }) => {
  test.setTimeout(200_000)
  await page.goto('/', { waitUntil: 'networkidle' })
  const marker = 'REGRESS-' + Date.now()
  await composer(page).fill(`Reply with exactly: ${marker}`)
  await composer(page).press('Enter')
  await expect.poll(async () => (await page.locator('body').innerText()).includes(marker),
    { timeout: 60_000, intervals: [1000] }).toBe(true)
  await expect.poll(async () => (await page.locator('body').innerText()).includes('Generating'),
    { timeout: 150_000, intervals: [2000] }).toBe(false)
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(3000)
  expect(await page.locator('body').innerText()).toContain(marker)
})

/* ── W-03 (High): interrupt is never honoured mid-turn ─────────────────────
 * The WS receive loop awaits thread.join for the whole turn, so the
 * interrupt frame is not read until the turn has already finished. */
test('W-03 interrupt stops token streaming and the server confirms it', async ({ page }) => {
  test.setTimeout(240_000)
  const recv: Array<{ t: number; type: string }> = []
  const t0 = Date.now()
  page.on('websocket', (ws) => {
    ws.on('framereceived', (f: any) => {
      try { recv.push({ t: Date.now() - t0, type: JSON.parse(f.payload).type }) } catch {}
    })
  })
  await page.goto('/', { waitUntil: 'networkidle' })
  await composer(page).fill('Write a 3000-word detailed essay on the history of computing.')
  await composer(page).press('Enter')
  await expect.poll(() => recv.some((e) => e.type === 'token'),
    { timeout: 90_000, intervals: [500] }).toBe(true)
  await page.getByRole('button', { name: 'Send' }).click()
  const cutoff = Date.now() - t0
  await page.waitForTimeout(15_000)
  const tokensAfter = recv.filter((e) => e.type === 'token' && e.t > cutoff + 1500).length
  const confirmed = recv.some((e) => e.type === 'interrupted')
  expect({ tokensAfter, confirmed }).toEqual({ tokensAfter: 0, confirmed: true })
})

/* ── W-04 (High): WebSocket loss is invisible ──────────────────────────────
 * wireSocket registers no onopen/onerror and no store holds a connection
 * flag, so the status bar keeps reading "ready" with a dead socket. */
test('W-04 a dead WebSocket is surfaced in the UI', async ({ page }) => {
  await page.route('**/ws', (r) => r.abort('failed'))
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(4000)
  expect(await page.locator('body').innerText())
    .toMatch(/disconnect|reconnect|offline|connection lost|not connected/i)
})

/* ── W-05 (High): a message sent into a dead socket is dropped silently ────
 * send() returns without queueing unless the socket is already CONNECTING,
 * while PromptBar has already flipped isStreaming - which also blocks every
 * later send, so the composer stays locked until a reload. */
test('W-05 sending with no socket warns instead of spinning forever', async ({ page }) => {
  await page.route('**/ws', (r) => r.abort('failed'))
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3000)
  await composer(page).fill('This must not vanish silently.')
  await composer(page).press('Enter')
  await page.waitForTimeout(4000)
  expect(await page.locator('body').innerText())
    .toMatch(/could not send|not connected|disconnect|offline|failed to send|retry/i)
})

/* ── W-10 (High): Schedules swallows a failed list query ───────────────────
 * list() throws on !r.ok, but the page only renders mutation.error, so a
 * 500 falls through to the "No scheduled tasks." empty state. */
test('W-10 Schedules distinguishes a load failure from an empty list', async ({ page }) => {
  await page.route('**/api/schedules*', (r) => r.fulfill({
    status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) }))
  await page.goto('/schedules', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3000)
  const body = await page.locator('body').innerText()
  expect(body).not.toContain('No scheduled tasks')
  expect(body.toLowerCase()).toMatch(/error|failed|could not/)
})

/* ── W-12 (Medium): malformed JSON leaks the raw parser exception ──────────
 * Affects Tools, Skills, Agents, MCP, Memory, Search, Artifacts, Fusion. */
const RAW_LEAK_PAGES: Array<[string, string]> = [
  ['/tools', '**/api/tools*'],
  ['/skills', '**/api/skills*'],
  ['/agents', '**/api/agents*'],
  ['/mcp', '**/api/mcp*'],
  ['/memory', '**/api/memory/status*'],
  ['/search', '**/api/search*'],
  ['/artifacts', '**/api/artifacts*'],
]
for (const [route, api] of RAW_LEAK_PAGES) {
  test(`W-12 ${route} explains a malformed response instead of leaking the parser error`, async ({ page }) => {
      await page.route(api, (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '{not json' }))
    await page.goto(route, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(2500)
    const body = await page.locator('body').innerText()
    expect(body).not.toMatch(/expected property name|in json at position|is not valid json|unexpected token/i)
    expect(body.toLowerCase()).toMatch(/error|failed|could not|unavailable/)
  })
}

/* ── W-13 (Medium): no error surface is announced to assistive tech ────────
 * Zero role="alert" / aria-live across all 14 pages in 52 fault runs. */
test('W-13 a load failure is announced via a live region', async ({ page }) => {
  await page.route('**/api/tools*', (r) => r.fulfill({ status: 500, body: '{}' , contentType: 'application/json' }))
  await page.goto('/tools', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2500)
  expect(await page.locator('[role="alert"], [role="status"], [aria-live]').count()).toBeGreaterThan(0)
})

/* ── W-14 (Medium): no retry affordance on any failed page ─────────────────*/
test('W-14 a failed page offers a retry control', async ({ page }) => {
  await page.route('**/api/tools*', (r) => r.fulfill({ status: 500, body: '{}', contentType: 'application/json' }))
  await page.goto('/tools', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2500)
  expect(await page.getByRole('button', { name: /retry|try again|reload/i }).count()).toBeGreaterThan(0)
})

/* ── W-15 (Medium): unknown routes render a blank document ────────────────
 * App.tsx has no catch-all <Route path="*">, and because AppLayout is the
 * parent route element nothing renders at all - not even the sidebar. */
test('W-15 an unknown route renders a 404 view, not a blank page', async ({ page }) => {
  await page.goto('/definitely-not-a-route-xyz', { waitUntil: 'networkidle' })
  const body = (await page.locator('body').innerText()).trim()
  expect(body.length).toBeGreaterThan(5)
})

/* ── W-16 (Low): the stop button keeps the accessible name "Send" ─────────*/
test('W-16 the stop control is named for the action it performs', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/', { waitUntil: 'networkidle' })
  await composer(page).fill('Write a 2000-word essay on the history of computing.')
  await composer(page).press('Enter')
  await expect.poll(async () => (await page.locator('body').innerText()).includes('Generating'),
    { timeout: 60_000, intervals: [500] }).toBe(true)
  expect(await page.getByRole('button', { name: /stop|interrupt|cancel/i }).count()).toBeGreaterThan(0)
})

/* ── W-17 (Low): Config shows a stale model_base_url ──────────────────────
 * /api/config returns engine.MODEL_BASE_URL, a module-level default that
 * activate_model never updates, so the row contradicts the active provider. */
test('W-17 Config Base URL matches the active provider', async ({ page }) => {
  const config = await (await page.request.get('/api/config')).json()
  const active = config.providers?.[config.active_provider]
  expect(config.model_base_url).toBe(active?.base_url)
})
