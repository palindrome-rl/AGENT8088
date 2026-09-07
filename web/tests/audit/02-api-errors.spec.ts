import { test } from '@playwright/test'
import fs from 'fs'
import { PAGE_PRIMARY, injectFault, type Fault } from './helpers'

const FAULTS: Fault[] = ['http500', 'abort', 'malformed', 'http401']
const OUT = 'audit-error-matrix.json'
const rows: any[] = []

/** Baseline body text per route with the API healthy, so we can tell whether a
 *  faulted render is actually distinguishable from the normal/empty view. */
const baseline: Record<string, string> = {}

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage()
  for (const { route } of PAGE_PRIMARY) {
    await page.goto(route, { waitUntil: 'networkidle' })
    await page.waitForTimeout(600)
    baseline[route] = (await page.locator('body').innerText()).replace(/\s+/g, ' ').trim()
  }
  await page.close()
})

for (const fault of FAULTS) {
  for (const { route, api, label } of PAGE_PRIMARY) {
    test(`[${fault}] ${label} ${route}`, async ({ page }) => {
      let hits = 0
      const bare = api.replace(/\*/g, '')
      page.on('request', (r) => { if (r.url().includes(bare)) hits++ })
      await injectFault(page, api, fault)
      const crashes: string[] = []
      page.on('pageerror', (e) => crashes.push(e.message))
      await page.goto(route, { waitUntil: 'domcontentloaded' })
      await page.waitForTimeout(2500)

      const body = (await page.locator('body').innerText()).replace(/\s+/g, ' ').trim()
      const hay = body.toLowerCase()
      const base = baseline[route] || ''

      const explained = /(error|fail|failed|could not|couldn't|unable|unavailable|problem|went wrong|denied)/.test(hay)
      const rawLeak = /(expected property name|unexpected token|is not valid json|in json at position|typeerror|undefined is not|networkerror|failed to fetch)/.test(hay)
      const why = /(500|401|404|status|because|reason|network|offline|timeout|timed out|disconnect|unauthorized|not found|server)/.test(hay)
      const nextAction = /(try again|retry|reload|refresh|check |verify |ensure |configure|contact|sign in|log in)/.test(hay)
      const retryBtn = await page.getByRole('button', { name: /retry|try again|reload|refresh/i }).count()
      const roleCount = await page.locator('[role="alert"], [role="status"], [aria-live]').count()
      // Did the faulted page look meaningfully different from the healthy one?
      const indistinguishable = body === base

      let verdict: string
      if (crashes.length) verdict = 'CRASH'
      else if (indistinguishable) verdict = 'SILENT_IDENTICAL_TO_HEALTHY'
      else if (!explained && rawLeak) verdict = 'RAW_EXCEPTION_LEAK'
      else if (!explained) verdict = 'SILENT_NO_MESSAGE'
      else if (explained && why && nextAction) verdict = 'GOOD'
      else verdict = 'PARTIAL'

      rows.push({ label, route, fault, verdict, intercepts: hits, explained, rawLeak, why, nextAction,
        retry: retryBtn > 0, a11yRole: roleCount > 0, crash: crashes[0] || null,
        body: body.slice(0, 300) })
    })
  }
}

test.afterAll(() => {
  fs.writeFileSync(OUT, JSON.stringify(rows, null, 2))
  console.log(`\nWROTE ${rows.length} rows to ${OUT}`)
})
