import { test, expect } from '@playwright/test'
import fs from 'fs'

const ROUTES = ['/', '/artifacts', '/tools', '/skills', '/agents', '/mcp', '/memory',
  '/sessions', '/config', '/doctor', '/fusion', '/tasks', '/search', '/schedules']
const findings: any[] = []

test('accessibility: controls have accessible names; inputs are labelled', async ({ page }) => {
  for (const route of ROUTES) {
    await page.goto(route, { waitUntil: 'networkidle' })
    await page.waitForTimeout(400)
    const r = await page.evaluate(() => {
      const namelessButtons: string[] = []
      document.querySelectorAll('button').forEach((b) => {
        const name = (b.getAttribute('aria-label') || b.textContent || '').trim()
        if (!name) namelessButtons.push(b.className.slice(0, 60) || '<button>')
      })
      const unlabelled: string[] = []
      document.querySelectorAll('input,textarea,select').forEach((el) => {
        const id = el.getAttribute('id')
        const has = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') ||
          (id && document.querySelector(`label[for="${id}"]`)) || el.closest('label') ||
          el.getAttribute('placeholder')
        if (!has) unlabelled.push((el.tagName + '.' + el.className).slice(0, 60))
      })
      const imgsNoAlt = [...document.querySelectorAll('img')].filter((i) => !i.hasAttribute('alt')).length
      const h1 = document.querySelectorAll('h1').length
      const liveRegions = document.querySelectorAll('[role="alert"],[role="status"],[aria-live]').length
      return { namelessButtons, unlabelled, imgsNoAlt, h1, liveRegions }
    })
    findings.push({ route, ...r })
  }
  console.log('\n=== A11Y ===')
  console.log('route         namelessBtns  unlabelledInputs  imgNoAlt  h1  liveRegions')
  findings.forEach((f) => console.log(
    `${f.route.padEnd(13)} ${String(f.namelessButtons.length).padEnd(13)} ${String(f.unlabelled.length).padEnd(17)} ${String(f.imgsNoAlt).padEnd(9)} ${String(f.h1).padEnd(3)} ${f.liveRegions}`))
  const withNameless = findings.filter((f) => f.namelessButtons.length)
  console.log('\nRoutes with nameless buttons:', withNameless.map((f) => `${f.route}(${f.namelessButtons.length})`).join(', ') || 'none')
  console.log('Routes with unlabelled inputs:', findings.filter(f=>f.unlabelled.length).map((f) => `${f.route}(${f.unlabelled.length})`).join(', ') || 'none')
  console.log('Routes with NO h1:', findings.filter(f=>f.h1===0).map(f=>f.route).join(', ') || 'none')
  fs.writeFileSync('audit-a11y.json', JSON.stringify(findings, null, 2))
})

test('keyboard: Tab reaches the composer and Cmd+K opens the palette', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' })
  await page.keyboard.press('Meta+k')
  await page.waitForTimeout(700)
  const paletteOpen = /command|search commands|type a command/i.test(await page.locator('body').innerText())
  console.log('Cmd+K opened a palette:', paletteOpen)
  await page.keyboard.press('Escape')
  // focus order
  const reached: string[] = []
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press('Tab')
    reached.push(await page.evaluate(() => document.activeElement?.tagName + ':' + (document.activeElement?.getAttribute('aria-label') || '')))
  }
  console.log('first 25 tab stops:', reached.join(' | ').slice(0, 500))
  expect(reached.some((r) => r.startsWith('TEXTAREA')), 'composer must be keyboard reachable').toBe(true)
})

test('responsive: no horizontal overflow at 375px / 768px / 1440px', async ({ page }) => {
  const bad: string[] = []
  for (const [w, h] of [[375, 812], [768, 1024], [1440, 900]] as const) {
    await page.setViewportSize({ width: w, height: h })
    for (const route of ROUTES) {
      await page.goto(route, { waitUntil: 'networkidle' })
      await page.waitForTimeout(300)
      const overflow = await page.evaluate(() =>
        document.documentElement.scrollWidth - document.documentElement.clientWidth)
      if (overflow > 4) bad.push(`${route} @${w}px overflows by ${overflow}px`)
    }
  }
  console.log('\n=== RESPONSIVE OVERFLOW ===')
  console.log(bad.join('\n') || 'no horizontal overflow at any breakpoint')
  expect(bad, bad.join('\n')).toHaveLength(0)
})
