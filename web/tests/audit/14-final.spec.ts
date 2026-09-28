import { test, expect } from '@playwright/test'

test('/help output actually renders in the chat transcript', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/', { waitUntil: 'networkidle' })
  const ta = page.locator('textarea').first()
  await ta.fill('/help')
  await ta.press('Enter')
  await page.waitForTimeout(4000)
  const body = await page.locator('body').innerText()
  const rendered = body.includes('Commands') && /\/tools|\/skills|\/doctor/.test(body)
  console.log('help output rendered:', rendered)
  console.log('snippet:', body.replace(/\s+/g, ' ').slice(0, 300))
  expect(rendered, '/help output missing from transcript').toBe(true)
})

test('chat history survives a hard refresh (session persistence)', async ({ page }) => {
  test.setTimeout(180_000)
  await page.goto('/', { waitUntil: 'networkidle' })
  const marker = 'PERSIST-PROBE-' + Date.now()
  const ta = page.locator('textarea').first()
  await ta.fill(`Reply with exactly: ${marker}`)
  await ta.press('Enter')
  await expect.poll(async () => (await page.locator('body').innerText()).includes('Generating'),
    { timeout: 30_000 }).toBe(true)
  await expect.poll(async () => (await page.locator('body').innerText()).includes('Generating'),
    { timeout: 150_000, intervals: [2000] }).toBe(false)
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(3000)
  const body = await page.locator('body').innerText()
  const survived = body.includes(marker)
  console.log('marker survived refresh:', survived)
  console.log('snippet:', body.replace(/\s+/g,' ').slice(0, 400))
  expect(survived, 'chat history lost after refresh').toBe(true)
})

test('unmatched /api path returns HTML instead of a JSON 404', async ({ page }) => {
  const r = await page.request.get('/api/definitely-not-an-endpoint')
  const ct = r.headers()['content-type'] || ''
  const text = (await r.text()).slice(0, 80)
  console.log(`status=${r.status()} content-type=${ct} body=${JSON.stringify(text)}`)
  expect(r.status(), 'unmatched /api path should be 404, not 200').toBe(404)
})
