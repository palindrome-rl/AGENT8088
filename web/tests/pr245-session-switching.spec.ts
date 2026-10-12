import { expect, test } from '@playwright/test'

test('switch chats during a turn, keep approval in its chat, queue for the other chat', async ({ page }) => {
  const ids = { 'chat-a': 'a'.repeat(32), 'chat-b': 'b'.repeat(32) }
  let viewed: keyof typeof ids = 'chat-a'
  let running = false
  let pending = false
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/sessions/resume') viewed = route.request().postDataJSON().name
    const approval = { type: 'escalation', id: 'approve-a', session_id: ids['chat-a'],
      tool_name: 'write_file', change_type: 'workspace-write', description: 'Write report.md' }
    const json = path === '/api/status' ? {
      session_name: viewed, session_id: ids[viewed], permission_mode: 'readonly',
      running_session: running ? { id: ids['chat-a'], name: 'chat-a' } : null,
    } : path === '/api/history' ? {
      session_id: ids[viewed], running: running && viewed === 'chat-a',
      messages: [{ role: 'user', content: viewed === 'chat-a' ? 'A original question' : 'B original question' }],
      pending_approvals: pending && viewed === 'chat-a' ? [approval] : [],
    } : path === '/api/sessions' ? Object.entries(ids).map(([name, id]) => ({ name, id,
      message_count: 1, updated: '', running: running && name === 'chat-a' }))
      : path === '/api/providers' ? { configured: [], builtins: [], active: '', details: {} }
      : path === '/api/sessions/resume' ? { ok: true, name: viewed, id: ids[viewed] } : []
    await route.fulfill({ json })
  })
  await page.addInitScript(() => {
    class Socket {
      static OPEN = 1
      readyState = 1
      onopen: any; onmessage: any; onclose: any
      constructor(url: string) {
        if (url.includes('/ws')) (window as any).__socket = this
        setTimeout(() => this.onopen?.({}), 0)
      }
      send(data: string) { ((window as any).__sent ??= []).push(JSON.parse(data)) }
      close() { this.readyState = 3 }
    }
    ;(window as any).WebSocket = Socket
  })
  const emit = (event: object) => page.evaluate(event =>
    (window as any).__socket.onmessage({ data: JSON.stringify(event) }), event)
  await page.goto('/', { waitUntil: 'networkidle' })
  await expect(page.getByText('A original question', { exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: 'Prompt' }).fill('Help me with A')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  running = true
  await emit({ type: 'spin', message: 'thinking...', session_id: ids['chat-a'] })
  await page.getByRole('button', { name: 'chat-b', exact: true }).click()
  await expect(page.getByText('B original question', { exact: true })).toBeVisible()
  pending = true
  await emit({ type: 'escalation', id: 'approve-a', session_id: ids['chat-a'],
    tool_name: 'write_file', change_type: 'workspace-write', description: 'Write report.md' })
  await expect(page.getByText('Approval Required', { exact: true })).toHaveCount(0)
  await page.getByRole('textbox', { name: 'Prompt' }).fill('Follow up in B')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.getByRole('button', { name: /chat-a/ }).first().click()
  await expect(page.getByText('Approval Required', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/pr245-background-approval.png', fullPage: true })
  await page.getByRole('button', { name: 'chat-b', exact: true }).click()
  await expect(page.getByText('B original question', { exact: true })).toBeVisible()
  running = false
  pending = false
  await emit({ type: 'answer', text: 'Only A result', session_id: ids['chat-a'] })
  await emit({ type: 'turn_complete', session_id: ids['chat-a'] })
  await expect(page.getByText('Only A result', { exact: true })).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => (window as any).__sent.at(-1))).toMatchObject({
    type: 'chat', text: 'Follow up in B', session_id: ids['chat-b'],
  })
  // Queue another prompt in the now-visible running chat. Completion of the
  // previous turn must not erase the new turn's Stop button/streaming state.
  await page.getByRole('textbox', { name: 'Prompt' }).fill('One more in B')
  await page.getByRole('button', { name: 'Add to queue', exact: true }).click()
  await emit({ type: 'turn_complete', session_id: ids['chat-b'] })
  await expect.poll(() => page.evaluate(() => (window as any).__sent.at(-1))).toMatchObject({
    type: 'chat', text: 'One more in B', session_id: ids['chat-b'],
  })
  await expect(page.getByRole('button', { name: 'Stop generating', exact: true })).toBeVisible()
})
