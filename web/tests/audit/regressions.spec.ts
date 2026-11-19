/**
 * End-to-end guards for the Web UI behaviours that are easy to regress:
 * session restore, interrupt, connection state, and how failures surface.
 *
 * Needs a running backend; point AUDIT_BASE at it (default 127.0.0.1:8280).
 * Run: npx playwright test -c playwright.audit.config.ts tests/audit/regressions.spec.ts
 */
import { test, expect } from '@playwright/test'

const composer = (page: any) => page.locator('textarea').first()

/* ── Session restore ───────────────────────────────────────────────────────
 * The messages live in /api/history and in the session file on disk, so a
 * reload only shows them if the mount-time syncSession() asks for history. */
test('chat transcript survives a hard refresh', async ({ page }) => {
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

/* ── Interrupt ─────────────────────────────────────────────────────────────
 * The WS receive loop has to stay readable while a turn runs. If it blocks on
 * the turn, the interrupt frame is only read once the turn is already over. */
test('interrupt stops token streaming and the server confirms it', async ({ page }) => {
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

/* ── Connection state ──────────────────────────────────────────────────────
 * wireSocket must register onopen/onerror and a store must hold the flag, or
 * the status bar goes on reading "ready" over a dead socket. */
test('a dead WebSocket is surfaced in the UI', async ({ page }) => {
  await page.route('**/ws', (r) => r.abort('failed'))
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(4000)
  expect(await page.locator('body').innerText())
    .toMatch(/disconnect|reconnect|offline|connection lost|not connected/i)
})

/* ── Send with no socket ───────────────────────────────────────────────────
 * send() only queues while the socket is CONNECTING, and PromptBar flips
 * isStreaming first - so a dropped send has to say so, or the composer stays
 * locked against every later send until a reload. */
test('sending with no socket warns instead of spinning forever', async ({ page }) => {
  await page.route('**/ws', (r) => r.abort('failed'))
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3000)
  await composer(page).fill('This must not vanish silently.')
  await composer(page).press('Enter')
  await page.waitForTimeout(4000)
  expect(await page.locator('body').innerText())
    .toMatch(/could not send|not connected|disconnect|offline|failed to send|retry/i)
})

/* ── Schedules load failure ────────────────────────────────────────────────
 * list() throws on !r.ok, so the page has to render that error and not only
 * mutation.error - otherwise a 500 reads as the "No scheduled tasks." empty
 * state. */
test('Schedules distinguishes a load failure from an empty list', async ({ page }) => {
  await page.route('**/api/schedules*', (r) => r.fulfill({
    status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) }))
  await page.goto('/schedules', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3000)
  const body = await page.locator('body').innerText()
  expect(body).not.toContain('No scheduled tasks')
  expect(body.toLowerCase()).toMatch(/error|failed|could not/)
})

/* ── Malformed responses ───────────────────────────────────────────────────
 * Every list page parses JSON, so each one needs its own guard against
 * showing the raw parser exception. */
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
  test(`${route} explains a malformed response instead of leaking the parser error`, async ({ page }) => {
      await page.route(api, (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '{not json' }))
    await page.goto(route, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(2500)
    const body = await page.locator('body').innerText()
    expect(body).not.toMatch(/expected property name|in json at position|is not valid json|unexpected token/i)
    expect(body.toLowerCase()).toMatch(/error|failed|could not|unavailable/)
  })
}

/* ── Errors reach assistive tech ───────────────────────────────────────────
 * An error that renders without role="alert" or aria-live is invisible to a
 * screen reader. */
test('a load failure is announced via a live region', async ({ page }) => {
  await page.route('**/api/tools*', (r) => r.fulfill({ status: 500, body: '{}' , contentType: 'application/json' }))
  await page.goto('/tools', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2500)
  expect(await page.locator('[role="alert"], [role="status"], [aria-live]').count()).toBeGreaterThan(0)
})

/* ── Recovery: a failed page offers a way forward, not just a message. ──────*/
test('a failed page offers a retry control', async ({ page }) => {
  await page.route('**/api/tools*', (r) => r.fulfill({ status: 500, body: '{}', contentType: 'application/json' }))
  await page.goto('/tools', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2500)
  expect(await page.getByRole('button', { name: /retry|try again|reload/i }).count()).toBeGreaterThan(0)
})

/* ── Unknown routes ────────────────────────────────────────────────────────
 * App.tsx needs a catch-all <Route path="*">. Without one, AppLayout is the
 * parent route element and nothing renders at all - not even the sidebar. */
test('an unknown route renders a 404 view, not a blank page', async ({ page }) => {
  await page.goto('/definitely-not-a-route-xyz', { waitUntil: 'networkidle' })
  const body = (await page.locator('body').innerText()).trim()
  expect(body.length).toBeGreaterThan(5)
})

/* ── The send/stop control is renamed when it changes what it does. ─────────*/
test('the stop control is named for the action it performs', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/', { waitUntil: 'networkidle' })
  await composer(page).fill('Write a 2000-word essay on the history of computing.')
  await composer(page).press('Enter')
  await expect.poll(async () => (await page.locator('body').innerText()).includes('Generating'),
    { timeout: 60_000, intervals: [500] }).toBe(true)
  expect(await page.getByRole('button', { name: /stop|interrupt|cancel/i }).count()).toBeGreaterThan(0)
})

/* ── Config reflects live state ────────────────────────────────────────────
 * /api/config returns engine.MODEL_BASE_URL, a module-level default; it has
 * to track activate_model or the row contradicts the active provider. */
test('Config Base URL matches the active provider', async ({ page }) => {
  const config = await (await page.request.get('/api/config')).json()
  const active = config.providers?.[config.active_provider]
  expect(config.model_base_url).toBe(active?.base_url)
})
