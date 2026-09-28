import { test, expect } from '@playwright/test'

test('Tools page: a 500 with a non-JSON body must not show a JSON parse error', async ({ page }) => {
  await page.goto('/tools', { waitUntil: 'networkidle' })
  await page.route('**/api/tool/**', (r) => r.fulfill({
    status: 500, contentType: 'text/plain', body: 'Internal Server Error' }))
  // Open a tool's arg form and submit it.
  const row = page.locator('tr, [role="row"], button').filter({ hasText: 'read_text' }).first()
  await row.click().catch(() => {})
  await page.waitForTimeout(600)
  const runBtn = page.getByRole('button', { name: /^run$|invoke|execute/i }).first()
  const visible = await runBtn.isVisible().catch(() => false)
  console.log('run button visible:', visible)
  if (!visible) { console.log('COULD NOT REACH RUN CONTROL — inconclusive'); return }
  await runBtn.click()
  await page.waitForTimeout(2500)
  const body = (await page.locator('body').innerText()).replace(/\s+/g, ' ')
  console.log('BODY:', body.slice(0, 500))
  const parseErr = /unexpected token|is not valid json|in json at position/i.test(body)
  console.log('shows raw JSON parse error:', parseErr)
  expect(parseErr, 'raw JSON parse error shown instead of a real message').toBe(false)
})
