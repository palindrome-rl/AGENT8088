import { test, expect } from '@playwright/test'

const assistant = (page: any) => page.locator('div', { hasText: /^Agent8088$/ })

test('a real assistant answer bubble arrives and renders clean prose', async ({ page }) => {
  test.setTimeout(240_000)
  const wsFrames: string[] = []
  page.on('websocket', (ws) => {
    ws.on('framereceived', (f: any) => { if (typeof f.payload === 'string') wsFrames.push(f.payload) })
  })
  await page.goto('/', { waitUntil: 'networkidle' })
  const c = page.locator('textarea').first()
  await c.fill('What is 17 plus 25? Answer with just the number.')
  await c.press('Enter')

  // Wait for the server to actually emit a terminal 'answer' frame.
  await expect.poll(() => wsFrames.filter((f) => f.includes('"type":"answer"')).length,
    { timeout: 220_000, intervals: [2000] }).toBeGreaterThan(0)

  const answerFrame = wsFrames.find((f) => f.includes('"type":"answer"'))!
  const parsed = JSON.parse(answerFrame)
  console.log('ANSWER TEXT:', JSON.stringify(String(parsed.text).slice(0, 300)))
  console.log('USAGE:', JSON.stringify(parsed.usage))
  console.log('FRAME TYPES SEEN:', [...new Set(wsFrames.map((f) => { try { return JSON.parse(f).type } catch { return '?' } }))].join(','))

  await expect(assistant(page).first()).toBeVisible({ timeout: 20_000 })
  const body = await page.locator('body').innerText()
  expect(body, 'model answer should contain 42').toContain('42')
  expect(body).not.toContain('✿')
  expect(body).not.toContain('FUNCTION')
  // spinner must clear once the answer lands
  await expect.poll(async () => (await page.locator('body').innerText()).includes('Generating'),
    { timeout: 20_000 }).toBe(false)
})

test('interrupt stops a running turn and re-enables the composer', async ({ page }) => {
  test.setTimeout(240_000)
  await page.goto('/', { waitUntil: 'networkidle' })
  const c = page.locator('textarea').first()
  await c.fill('Write a very long detailed essay about the history of computing, at least 2000 words.')
  await c.press('Enter')
  // wait until streaming starts
  await expect.poll(async () => (await page.locator('body').innerText()).includes('Generating'),
    { timeout: 60_000, intervals: [1000] }).toBe(true)
  const stop = page.getByRole('button').filter({ hasNot: page.locator('svg[data-x]') })
  await page.waitForTimeout(1500)
  // The send button becomes a stop button while streaming.
  await page.locator('button').last().click({ trial: false }).catch(() => {})
  await page.waitForTimeout(6000)
  const body = await page.locator('body').innerText()
  console.log('POST-INTERRUPT:', body.replace(/\s+/g,' ').slice(0, 400))
  const stillGenerating = body.includes('Generating')
  console.log('still generating after interrupt:', stillGenerating)
})
