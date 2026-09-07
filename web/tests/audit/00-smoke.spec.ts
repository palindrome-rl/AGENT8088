import { test, expect } from '@playwright/test'

const ROUTES = ['/', '/artifacts', '/tools', '/skills', '/agents', '/mcp', '/memory',
  '/sessions', '/config', '/doctor', '/fusion', '/tasks', '/search', '/schedules']

test('every route renders without a console error or crash', async ({ page }) => {
  const problems: string[] = []
  for (const route of ROUTES) {
    const errors: string[] = []
    page.on('console', (m) => { if (m.type() === 'error') errors.push(`${route}: ${m.text()}`) })
    page.on('pageerror', (e) => errors.push(`${route}: PAGEERROR ${e.message}`))
    await page.goto(route, { waitUntil: 'networkidle' })
    const body = (await page.locator('body').innerText()).trim()
    if (body.length < 5) problems.push(`${route}: rendered blank (${body.length} chars)`)
    if (/Something went wrong|Unhandled|Cannot read/i.test(body)) problems.push(`${route}: crash text -> ${body.slice(0, 200)}`)
    problems.push(...errors)
    page.removeAllListeners('console'); page.removeAllListeners('pageerror')
  }
  console.log('SMOKE PROBLEMS:\n' + (problems.join('\n') || '(none)'))
  expect(problems, problems.join('\n')).toHaveLength(0)
})
