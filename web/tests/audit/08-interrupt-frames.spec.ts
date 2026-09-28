import { test, expect } from '@playwright/test'

test('interrupt: server confirms with an interrupted frame', async ({ page }) => {
  test.setTimeout(240_000)
  const log: Array<{ t: number; dir: string; type: string }> = []
  const t0 = Date.now()
  page.on('websocket', (ws) => {
    ws.on('framereceived', (f: any) => {
      try { log.push({ t: Date.now()-t0, dir: 'recv', type: JSON.parse(f.payload).type }) } catch {}
    })
    ws.on('framesent', (f: any) => {
      try { log.push({ t: Date.now()-t0, dir: 'sent', type: JSON.parse(f.payload).type }) } catch {}
    })
  })
  await page.goto('/', { waitUntil: 'networkidle' })
  await page.locator('textarea').first().fill('Write a 3000-word essay on the history of computing.')
  await page.locator('textarea').first().press('Enter')
  await expect.poll(async () => (await page.locator('body').innerText()).includes('Generating'),
    { timeout: 60_000, intervals: [500] }).toBe(true)
  await page.waitForTimeout(2500)
  await page.getByRole('button', { name: 'Send' }).click()
  const interruptAt = Date.now() - t0
  await page.waitForTimeout(20_000)   // give the server ample time to confirm
  console.log('interrupt clicked at t=' + interruptAt + 'ms')
  console.log('FRAME LOG (deduped consecutive tokens):')
  let lastType = ''
  for (const e of log) {
    if (e.type === 'token' && lastType === 'token') continue
    console.log(`  t=${e.t}ms ${e.dir} ${e.type}`)
    lastType = e.type
  }
  const gotInterrupted = log.some((e) => e.dir === 'recv' && e.type === 'interrupted')
  const tokensAfter = log.filter((e) => e.dir === 'recv' && e.type === 'token' && e.t > interruptAt).length
  console.log(`received 'interrupted' frame: ${gotInterrupted}`)
  console.log(`token frames still arriving AFTER interrupt: ${tokensAfter}`)
  const stillGenerating = (await page.locator('body').innerText()).includes('Generating')
  console.log(`UI still shows Generating: ${stillGenerating}`)
})
