import { test, expect } from '@playwright/test'

test('escalation approval round-trip completes (or is shown to deadlock)', async ({ page }) => {
  test.setTimeout(280_000)
  await page.goto('/', { waitUntil: 'networkidle' })
  // Switch to a mode that escalates instead of auto-approving.
  const modeResp = await page.request.post('/api/mode', { data: { mode: 'default' } })
  console.log('mode switch:', modeResp.status(), (await modeResp.text()).slice(0, 200))

  const log: string[] = []
  const t0 = Date.now()
  page.on('websocket', (ws) => {
    ws.on('framereceived', (f: any) => {
      try { const d = JSON.parse(f.payload); if (!['token','spin'].includes(d.type)) log.push(`${Date.now()-t0}ms recv ${d.type}`) } catch {}
    })
    ws.on('framesent', (f: any) => {
      try { log.push(`${Date.now()-t0}ms sent ${JSON.parse(f.payload).type}`) } catch {}
    })
  })
  await page.reload({ waitUntil: 'networkidle' })
  await page.locator('textarea').first().fill('Create a file called approval-probe.txt containing the word HELLO in the project directory.')
  await page.locator('textarea').first().press('Enter')

  // Wait for an approval card to appear.
  const appeared = await page.getByRole('button', { name: /approve|allow|yes/i }).first()
    .waitFor({ timeout: 150_000 }).then(() => true).catch(() => false)
  console.log('approval card appeared:', appeared)

  if (appeared) {
    const clickAt = Date.now() - t0
    await page.getByRole('button', { name: /approve|allow|yes/i }).first().click()
    console.log(`clicked approve at ${clickAt}ms`)
    const resolved = await expect.poll(async () =>
      (await page.locator('body').innerText()).includes('Generating') === false,
      { timeout: 90_000, intervals: [2000] }).toBe(true).then(() => true).catch(() => false)
    console.log('turn resolved after approval:', resolved)
  }
  console.log('=== FRAME LOG ===')
  log.forEach((l) => console.log('  ' + l))
  const body = (await page.locator('body').innerText()).replace(/\s+/g, ' ')
  console.log('BODY:', body.slice(0, 500))
})
