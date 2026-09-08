import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

/* The queue is client-side: prompts typed during a turn wait their turn and are
 * dispatched when it ends. Driving that against a real model would be slow and
 * non-deterministic, so these specs replace window.WebSocket with a stub the
 * test controls - every frame the app receives is one the test chose to send,
 * and every frame the app sends is recorded for assertion. */

declare global {
  interface Window {
    __wsSent: string[]
    __wsPush: (event: Record<string, unknown>) => void
  }
}

/** Install the WebSocket stub and stub the REST calls the composer makes. */
async function bootWithFakeSocket(page: Page) {
  await page.addInitScript(() => {
    window.__wsSent = []
    // React StrictMode mounts twice, so the app opens a socket, closes it, and
    // opens another. A real browser delivers frames only to the live socket -
    // tracking open ones keeps the stub from double-delivering to the closed
    // one, which would run every frame handler twice.
    const open = new Set<{ deliver: (e: MessageEvent) => void }>()

    class FakeWebSocket {
      static readonly CONNECTING = 0
      static readonly OPEN = 1
      static readonly CLOSING = 2
      static readonly CLOSED = 3
      readonly OPEN = 1
      readyState = 1
      onopen: ((e: Event) => void) | null = null
      onmessage: ((e: MessageEvent) => void) | null = null
      onclose: ((e: CloseEvent) => void) | null = null
      onerror: ((e: Event) => void) | null = null
      private entry = { deliver: (e: MessageEvent) => this.onmessage?.(e) }

      constructor(public url: string) {
        open.add(this.entry)
        setTimeout(() => this.onopen?.(new Event('open')), 0)
      }

      send(data: string) { window.__wsSent.push(data) }
      close() {
        this.readyState = 3
        open.delete(this.entry)
      }
      addEventListener() { /* the app uses the on* properties */ }
      removeEventListener() { /* the app uses the on* properties */ }
    }

    window.WebSocket = FakeWebSocket as unknown as typeof WebSocket

    window.__wsPush = (event) => {
      const message = new MessageEvent('message', { data: JSON.stringify(event) })
      open.forEach(({ deliver }) => deliver(message))
    }
  })

  // A session already exists, so the composer never has to create one.
  await page.route('**/api/status', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ session_name: 'queue-spec', permission_mode: 'readonly' }),
  }))

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('textbox', { name: 'Prompt' })).toBeVisible()
}

/** Frames the app sent, parsed. */
async function sent(page: Page) {
  return page.evaluate(() => window.__wsSent.map((raw) => JSON.parse(raw)))
}

async function push(page: Page, event: Record<string, unknown>) {
  await page.evaluate((e) => window.__wsPush(e), event)
}

/** Put the UI into a running turn. */
async function startTurn(page: Page) {
  await push(page, { type: 'spin', message: 'thinking...', elapsed: 0, tokens: 0 })
  await expect(page.getByRole('button', { name: 'Stop generating' })).toBeVisible()
}

async function type(page: Page, text: string) {
  const box = page.getByRole('textbox', { name: 'Prompt' })
  await box.fill(text)
  await box.press('Enter')
}

test('a prompt typed during a turn is queued instead of sent', async ({ page }) => {
  await bootWithFakeSocket(page)
  await startTurn(page)

  await type(page, 'second prompt')

  await expect(page.getByRole('listitem').filter({ hasText: 'second prompt' })).toBeVisible()
  const frames = await sent(page)
  expect(frames.filter((f) => f.type === 'chat')).toHaveLength(0)
})

test('the queue dispatches the next prompt when the turn ends', async ({ page }) => {
  await bootWithFakeSocket(page)
  await startTurn(page)
  await type(page, 'queued one')
  await type(page, 'queued two')

  await push(page, { type: 'answer', text: 'done', usage: { seconds: 1, tokens: 1 } })
  await expect(page.getByRole('listitem').filter({ hasText: 'queued one' })).toBeVisible()
  expect((await sent(page)).filter((f) => f.type === 'chat')).toHaveLength(0)
  await push(page, { type: 'turn_complete' })

  await expect.poll(async () => (await sent(page)).filter((f) => f.type === 'chat').length)
    .toBe(1)
  const frames = await sent(page)
  expect(frames.filter((f) => f.type === 'chat')[0].text).toBe('queued one')
  // Only the head goes out; the tail waits for the next turn to end.
  await expect(page.getByRole('listitem').filter({ hasText: 'queued two' })).toBeVisible()
})

test('a queued prompt can be edited before it is sent', async ({ page }) => {
  await bootWithFakeSocket(page)
  await startTurn(page)
  await type(page, 'orignal typo')

  const row = page.getByRole('listitem').filter({ hasText: 'orignal typo' })
  await row.getByRole('button', { name: /edit/i }).click()
  const editor = page.getByRole('textbox', { name: /edit queued prompt/i })
  await editor.fill('corrected prompt')
  await editor.press('Enter')

  await expect(page.getByRole('listitem').filter({ hasText: 'corrected prompt' })).toBeVisible()

  await push(page, { type: 'answer', text: 'done', usage: { seconds: 1, tokens: 1 } })
  await push(page, { type: 'turn_complete' })
  await expect.poll(async () => (await sent(page)).filter((f) => f.type === 'chat').length).toBe(1)
  const frames = await sent(page)
  expect(frames.filter((f) => f.type === 'chat')[0].text).toBe('corrected prompt')
})

test('editing a queued prompt can be abandoned with Escape', async ({ page }) => {
  await bootWithFakeSocket(page)
  await startTurn(page)
  await type(page, 'keep me')

  const row = page.getByRole('listitem').filter({ hasText: 'keep me' })
  await row.getByRole('button', { name: /edit/i }).click()
  const editor = page.getByRole('textbox', { name: /edit queued prompt/i })
  await editor.fill('discard this')
  await editor.press('Escape')

  await expect(page.getByRole('listitem').filter({ hasText: 'keep me' })).toBeVisible()
  await expect(page.getByRole('listitem').filter({ hasText: 'discard this' })).toHaveCount(0)
})

test('a queued prompt can be removed', async ({ page }) => {
  await bootWithFakeSocket(page)
  await startTurn(page)
  await type(page, 'delete me')
  await type(page, 'keep me')

  await page.getByRole('listitem').filter({ hasText: 'delete me' })
    .getByRole('button', { name: /remove/i }).click()

  await expect(page.getByRole('listitem').filter({ hasText: 'delete me' })).toHaveCount(0)
  await expect(page.getByRole('listitem').filter({ hasText: 'keep me' })).toBeVisible()
})

test('an error pauses the queue and keeps the prompts', async ({ page }) => {
  await bootWithFakeSocket(page)
  await startTurn(page)
  await type(page, 'still queued')

  await push(page, { type: 'error', message: 'the turn blew up' })

  await expect(page.getByRole('listitem').filter({ hasText: 'still queued' })).toBeVisible()
  await expect(page.getByRole('button', { name: /resume queue/i })).toBeVisible()
  expect((await sent(page)).filter((f) => f.type === 'chat')).toHaveLength(0)
})

test('an interrupt pauses the queue rather than draining it', async ({ page }) => {
  await bootWithFakeSocket(page)
  await startTurn(page)
  await type(page, 'not yet')

  await push(page, { type: 'interrupted', elapsed: 1, partial: '' })

  await expect(page.getByRole('button', { name: /resume queue/i })).toBeVisible()
  expect((await sent(page)).filter((f) => f.type === 'chat')).toHaveLength(0)
})

test('Stop clears the generating state immediately and ignores late frames', async ({ page }) => {
  await bootWithFakeSocket(page)
  await startTurn(page)

  await page.getByRole('button', { name: 'Stop generating' }).click()

  expect((await sent(page)).some((frame) => frame.type === 'interrupt')).toBe(true)
  await expect(page.getByRole('button', { name: 'Stop generating' })).toHaveCount(0)
  await push(page, { type: 'spin', message: 'thinking...', elapsed: 1, tokens: 1 })
  await expect(page.getByRole('button', { name: 'Stop generating' })).toHaveCount(0)
})

test('resuming a paused queue dispatches the next prompt', async ({ page }) => {
  await bootWithFakeSocket(page)
  await startTurn(page)
  await type(page, 'run me later')
  await push(page, { type: 'error', message: 'boom' })

  await page.getByRole('button', { name: /resume queue/i }).click()

  await expect.poll(async () => (await sent(page)).filter((f) => f.type === 'chat').length).toBe(1)
  const frames = await sent(page)
  expect(frames.filter((f) => f.type === 'chat')[0].text).toBe('run me later')
})

test('a paused queue can be cleared', async ({ page }) => {
  await bootWithFakeSocket(page)
  await startTurn(page)
  await type(page, 'throw me away')
  await push(page, { type: 'error', message: 'boom' })

  await page.getByRole('button', { name: /clear queue/i }).click()

  await expect(page.getByRole('listitem').filter({ hasText: 'throw me away' })).toHaveCount(0)
  expect((await sent(page)).filter((f) => f.type === 'chat')).toHaveLength(0)
})

test('slash commands queue too and dispatch as commands', async ({ page }) => {
  await bootWithFakeSocket(page)
  await startTurn(page)
  await type(page, '/status')

  await expect(page.getByRole('listitem').filter({ hasText: '/status' })).toBeVisible()

  await push(page, { type: 'answer', text: 'done', usage: { seconds: 1, tokens: 1 } })
  await push(page, { type: 'turn_complete' })

  await expect.poll(async () => (await sent(page)).filter((f) => f.type === 'command').length).toBe(1)
  const frames = await sent(page)
  expect(frames.filter((f) => f.type === 'command')[0].command).toBe('status')
})

test('with no turn running a prompt still sends immediately', async ({ page }) => {
  await bootWithFakeSocket(page)

  await type(page, 'straight through')

  await expect.poll(async () => (await sent(page)).filter((f) => f.type === 'chat').length).toBe(1)
  await expect(page.getByRole('listitem').filter({ hasText: 'straight through' })).toHaveCount(0)
})

test('the model picker sits in the content-pane header, not the sidebar', async ({ page }) => {
  await page.route('**/api/providers', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ configured: ['ollama'], builtins: [], active: 'ollama' }),
  }))
  await page.route('**/api/models/ollama', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['qwen3'] }),
  }))
  await page.route('**/api/model/switch', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, provider: 'ollama', model: 'qwen3' }),
  }))
  await bootWithFakeSocket(page)
  await push(page, { type: 'status', data: { provider: 'ollama', model: 'qwen2.5', permission_mode: 'readonly', context_pct: 0, session_name: 'queue-spec' } })

  const trigger = page.getByRole('button', { name: 'Change model' })
  await expect(trigger).toBeVisible()
  await expect(trigger).toHaveCount(1)
  // It moved out of the navigation rail and into the header above the chat.
  await expect(page.locator('aside').getByRole('button', { name: 'Change model' })).toHaveCount(0)
  // The trigger shows the bare model name; the provider stays in the tooltip.
  await expect(trigger).toHaveText('qwen2.5')
  await expect(trigger).toHaveAttribute('title', 'ollama:qwen2.5')

  // Scope to the switcher: "Model" as a bare substring also matches the
  // sidebar's session-action buttons now that this control is page-level.
  const switcher = page.locator('[data-model-menu]')
  await trigger.click()
  await switcher.getByLabel('Provider', { exact: true }).selectOption('ollama')
  await switcher.getByLabel('Model', { exact: true }).fill('qwen3')
  await switcher.getByRole('button', { name: 'Switch model' }).click()
  await expect(trigger).toHaveText('qwen3')
})

test('the header keeps the model picker on a settings page, beside Back to chat', async ({ page }) => {
  await page.route('**/api/providers', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ configured: ['ollama'], builtins: [], active: 'ollama' }),
  }))
  await bootWithFakeSocket(page)
  // addInitScript survives navigation, so the socket stub is reinstalled here.
  await page.goto('/appearance', { waitUntil: 'domcontentloaded' })

  await expect(page.getByRole('button', { name: 'Back to chat' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Change model' })).toBeVisible()
})
