import { expect, test } from '@playwright/test'

test('credential notices render only in their chat and are not replayed from history', async ({ page }) => {
  const sessionId = 'a'.repeat(32)
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const json = path === '/api/status' ? {
      session_name: 'review', session_id: sessionId, permission_mode: 'readonly', running_session: null,
    } : path === '/api/history' ? {
      session_id: sessionId, running: false,
      messages: [
        { role: 'system', content: 'Private system instructions must stay hidden' },
        { role: 'user', content: 'Check a synthetic credential' },
      ],
    } : path === '/api/providers' ? { configured: [], builtins: [], active: '', details: {} } : []
    await route.fulfill({ json })
  })
  await page.addInitScript(() => {
    class Socket {
      static OPEN = 1
      readyState = 1
      onopen?: () => void
      onmessage?: (event: { data: string }) => void
      constructor() {
        ;(window as any).__noticeSocket = this
        setTimeout(() => this.onopen?.(), 0)
      }
      send() {}
      close() { this.readyState = 3 }
    }
    ;(window as any).WebSocket = Socket
  })
  await page.goto('/', { waitUntil: 'networkidle' })
  await expect(page.getByText('Check a synthetic credential', { exact: true })).toBeVisible()
  await expect(page.getByText('Private system instructions must stay hidden')).toHaveCount(0)
  const emit = (event: object) => page.evaluate(event =>
    (window as any).__noticeSocket.onmessage({ data: JSON.stringify(event) }), event)
  await emit({ type: 'notice', level: 'warning', session_id: 'b'.repeat(32), text: 'Other chat notice' })
  await expect(page.getByText('Other chat notice')).toHaveCount(0)
  const masked = 'Your message: possible credential sk-p...6789; revoke it if real.'
  await emit({ type: 'notice', level: 'warning', session_id: sessionId, text: masked })
  await expect(page.getByRole('status').filter({ hasText: masked })).toBeVisible()
  await emit({ type: 'answer', session_id: sessionId, text: 'The turn continues after its advisory warning.' })
  await expect(page.getByText('The turn continues after its advisory warning.')).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: masked })).toBeVisible()
  await page.reload({ waitUntil: 'networkidle' })
  await expect(page.getByText('Check a synthetic credential', { exact: true })).toBeVisible()
  await expect(page.getByText(masked)).toHaveCount(0)
})
