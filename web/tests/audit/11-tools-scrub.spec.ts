import { test, expect } from '@playwright/test'

test('Tools page must not leak tool-call protocol markup into the result panel', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/tools', { waitUntil: 'networkidle' })
  // Force a result that legitimately contains the protocol sentinel.
  await page.route('**/api/tool/**', (r) => r.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ name: 'read_text',
      result: "Error: 'read_text' was called with no arguments. Send an ✿ARGS✿ block containing 'filename', e.g. ✿ARGS✿: {\"filename\": \"...\"}" }),
  }))
  // Find and run any tool.
  const runBtn = page.getByRole('button', { name: /run|invoke|execute/i }).first()
  const hasRun = await runBtn.isVisible().catch(() => false)
  if (!hasRun) {
    // open the first tool row, then run
    await page.locator('button').filter({ hasText: /read_text|shell|write/ }).first().click().catch(() => {})
    await page.waitForTimeout(500)
  }
  await page.getByRole('button', { name: /run|invoke|execute/i }).first().click()
  await page.waitForTimeout(2500)
  const body = await page.locator('body').innerText()
  const leaked = body.includes('✿')
  console.log('flower sentinel present in DOM:', leaked)
  console.log('ARGS text present:', body.includes('ARGS'))
  console.log('snippet:', body.replace(/\s+/g,' ').slice(0, 400))
  expect(leaked, 'tool-call protocol sentinel leaked into the Tools page').toBe(false)
})
