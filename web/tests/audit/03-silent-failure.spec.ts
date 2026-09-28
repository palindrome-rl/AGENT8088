import { test, expect } from '@playwright/test'

/** Verify the fault actually fired before judging the page. */
async function faultAndCount(page: any, pattern: string, route: string) {
  let hits = 0
  await page.route(pattern, async (r: any) => {
    hits++
    await r.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) })
  })
  await page.goto(route, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3000)
  const body = (await page.locator('body').innerText()).replace(/\s+/g, ' ')
  return { hits, body }
}

test('Schedules: 500 on list is swallowed and shown as an empty state', async ({ page }) => {
  const { hits, body } = await faultAndCount(page, '**/api/schedules*', '/schedules')
  console.log(`intercepts=${hits}`)
  console.log(`body=${body.slice(0, 320)}`)
  expect(hits, 'fault must actually fire').toBeGreaterThan(0)
  // Document the defect: empty-state copy shown, no error text.
  expect(body).toContain('No scheduled tasks')
  expect(body.toLowerCase()).not.toMatch(/error|failed|http 500/)
})

test('Tasks: 500 on list surfaces an error (glob must cover the query string)', async ({ page }) => {
  const { hits, body } = await faultAndCount(page, '**/api/tasks*', '/tasks')
  console.log(`intercepts=${hits}`)
  console.log(`body=${body.slice(0, 320)}`)
  expect(hits, 'fault must actually fire').toBeGreaterThan(0)
  expect(body).toContain('HTTP 500')
})
