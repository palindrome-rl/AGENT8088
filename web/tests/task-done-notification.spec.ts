import { expect, test } from '@playwright/test'

// Feature suggestion: notify when a task finishes while the person is elsewhere.
test('a finished task notifies only when the tab is not in focus', async ({ page }) => {
  const sessionId = 'c'.repeat(32)
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const json = path === '/api/status' ? {
      session_name: 'invoice run', session_id: sessionId, permission_mode: 'readonly', running_session: null,
    } : path === '/api/history' ? { session_id: sessionId, running: false, messages: [] }
      : path === '/api/providers' ? { configured: [], builtins: [], active: '', details: {} } : []
    await route.fulfill({ json })
  })
  await page.addInitScript(() => {
    class Socket {
      static OPEN = 1
      readyState = 1
      onopen?: () => void
      onmessage?: (event: { data: string }) => void
      constructor() {
        ;(window as any).__doneSocket = this
        setTimeout(() => this.onopen?.(), 0)
      }
      send() {}
      close() { this.readyState = 3 }
    }
    ;(window as any).WebSocket = Socket
    const shown: { title: string; body: string }[] = []
    ;(window as any).__shown = shown
    ;(window as any).__focused = true
    class FakeNotification {
      static permission = 'granted'
      static requestPermission() { return Promise.resolve('granted') }
      onclick: (() => void) | null = null
      constructor(title: string, options: { body: string }) { shown.push({ title, body: options.body }) }
      close() {}
    }
    ;(window as any).Notification = FakeNotification
    document.hasFocus = () => (window as any).__focused
  })
  await page.goto('/', { waitUntil: 'networkidle' })
  const finish = () => page.evaluate(id =>
    (window as any).__doneSocket.onmessage({ data: JSON.stringify({ type: 'turn_complete', session_id: id }) }),
  sessionId)

  await finish()
  expect(await page.evaluate(() => (window as any).__shown.length)).toBe(0)

  await page.evaluate(() => { (window as any).__focused = false })
  await finish()
  const shown = await page.evaluate(() => (window as any).__shown)
  expect(shown).toEqual([{ title: 'Agent8088 finished', body: '"invoice run" is ready.' }])
})
