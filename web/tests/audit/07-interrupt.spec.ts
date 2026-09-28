import { test, expect } from '@playwright/test'

test('interrupt stops a running turn and re-enables the composer', async ({ page }) => {
  test.setTimeout(240_000)
  const frames: string[] = []
  page.on('websocket', (ws) => {
    ws.on('framereceived', (f: any) => { if (typeof f.payload === 'string') frames.push(f.payload) })
    ws.on('framesent', (f: any) => { if (typeof f.payload === 'string') frames.push('SENT:' + f.payload) })
  })
  await page.goto('/', { waitUntil: 'networkidle' })
  await page.locator('textarea').first().fill('Write a 3000-word detailed essay on the history of computing.')
  await page.locator('textarea').first().press('Enter')

  await expect.poll(async () => (await page.locator('body').innerText()).includes('Generating'),
    { timeout: 60_000, intervals: [500] }).toBe(true)
  await page.waitForTimeout(2000)

  const btn = page.getByRole('button', { name: 'Send' })
  await btn.click()
  console.log('interrupt frame sent:', frames.some((f) => f.startsWith('SENT:') && f.includes('interrupt')))

  await expect.poll(async () => (await page.locator('body').innerText()).includes('Generating'),
    { timeout: 60_000, intervals: [1000] }).toBe(false)

  console.log('server frames after interrupt:', [...new Set(frames.filter(f=>!f.startsWith('SENT:')).map((f) => { try { return JSON.parse(f).type } catch { return '?' } }))].join(','))
  // composer must accept input again
  await expect(page.getByRole('button', { name: 'Send' })).toBeEnabled()
  await page.locator('textarea').first().fill('follow-up works')
  expect(await page.locator('textarea').first().inputValue()).toBe('follow-up works')
})
