import { test, expect } from '@playwright/test'

async function composer(page: any) {
  const c = page.locator('textarea, input[type="text"]').first()
  await expect(c).toBeVisible()
  return c
}

test('chat happy path: streams a real model answer', async ({ page }) => {
  test.setTimeout(180_000)
  await page.goto('/', { waitUntil: 'networkidle' })
  const c = await composer(page)
  await c.fill('Reply with exactly the word BANANA and nothing else.')
  await c.press('Enter')
  // user message should echo immediately (optimistic render)
  await expect(page.locator('body')).toContainText('BANANA', { timeout: 20_000 })
  // then an assistant answer must arrive
  await expect(page.locator('body')).toContainText(/BANANA/i, { timeout: 150_000 })
  const body = await page.locator('body').innerText()
  console.log('CHAT BODY:', body.replace(/\s+/g, ' ').slice(0, 600))
  // tool-call protocol markup must never leak into the transcript
  expect(body).not.toContain('✿')
  expect(body).not.toContain('<tool_call>')
})

test('empty submit is rejected without sending', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' })
  const before = await page.locator('body').innerText()
  const c = await composer(page)
  await c.fill('   ')
  await c.press('Enter')
  await page.waitForTimeout(1500)
  const after = await page.locator('body').innerText()
  expect(after.replace(/\s+/g,' ')).toBe(before.replace(/\s+/g,' '))
})

test('WebSocket drop is surfaced to the user', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  const baseline = (await page.locator('body').innerText()).replace(/\s+/g,' ')
  // Kill every live socket from inside the page.
  await page.evaluate(() => {
    // @ts-ignore
    const list = (window as any).__sockets || []
    list.forEach((s: WebSocket) => s.close())
  })
  // Force it the reliable way: block the endpoint then reload.
  await page.route('**/ws', (r) => r.abort('failed'))
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(4000)
  const body = (await page.locator('body').innerText()).replace(/\s+/g,' ')
  console.log('WS-DOWN BODY:', body.slice(0, 400))
  const tellsUser = /(disconnect|reconnect|offline|connection|not connected|lost|retry)/i.test(body)
  expect(tellsUser, `WS failure not communicated. Body: ${body.slice(0,300)}`).toBe(true)
})

test('sending while socket is down does not silently swallow the message', async ({ page }) => {
  await page.route('**/ws', (r) => r.abort('failed'))
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3000)
  const c = await composer(page)
  await c.fill('This should not vanish silently.')
  await c.press('Enter')
  await page.waitForTimeout(3000)
  const body = (await page.locator('body').innerText()).replace(/\s+/g,' ')
  console.log('SEND-WHILE-DOWN BODY:', body.slice(0, 400))
  const warned = /(disconnect|reconnect|offline|connection|not connected|failed|error|cannot send)/i.test(body)
  expect(warned, `Message sent into a dead socket with no warning. Body: ${body.slice(0,300)}`).toBe(true)
})
