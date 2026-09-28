import { test } from '@playwright/test'

/** Decisive: is any client->server frame processed while a turn is running? */
test('WS message loop is blocked for the duration of a chat turn', async ({ page }) => {
  test.setTimeout(300_000)
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const result = await page.evaluate(async () => {
    const log: string[] = []
    const t0 = Date.now()
    const ws = new WebSocket(`ws://${location.host}/ws`)
    await new Promise((res, rej) => { ws.onopen = () => res(null); ws.onerror = () => rej(new Error('ws failed')); setTimeout(() => rej(new Error('ws open timeout')), 10000) })
    ws.onmessage = (e) => {
      const d = JSON.parse(e.data)
      if (d.type !== 'token' && d.type !== 'spin') log.push(`${Date.now() - t0}ms recv ${d.type}`)
    }
    // Start a long turn.
    ws.send(JSON.stringify({ type: 'chat', text: 'Write a 3000-word essay on the history of computing.' }))
    log.push(`${Date.now() - t0}ms sent chat`)
    await new Promise((r) => setTimeout(r, 4000))
    // Now send a trivial command that normally answers instantly.
    ws.send(JSON.stringify({ type: 'command', command: 'status', args: '' }))
    log.push(`${Date.now() - t0}ms sent command:/status`)
    ws.send(JSON.stringify({ type: 'interrupt' }))
    log.push(`${Date.now() - t0}ms sent interrupt`)
    // Wait long enough for the whole turn to finish.
    await new Promise((r) => setTimeout(r, 90_000))
    log.push(`${Date.now() - t0}ms done waiting`)
    ws.close()
    return log
  })
  console.log('=== WS TIMELINE ===')
  result.forEach((l) => console.log('  ' + l))
})
