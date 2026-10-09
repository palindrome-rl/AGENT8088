import { test, expect } from '@playwright/test'

/**
 * Critical QA issues, 2026-10 round:
 *
 *  - "the 'Web search' item in the + menu points at the wrong search. It only
 *     types /search into the box. On the backend, /search is the command for
 *     configuring the search backend. It does not run a search."
 *  - "Attached a file with a message and added web search from the + menu.
 *     Web search added '/search' in the message. A message starting with / is
 *     sent as a command and the attachments are ignored."
 */

const composer = (page: import('@playwright/test').Page) =>
  page.locator('[data-promptbar] textarea, [data-promptbar] input[type="text"]').first()

async function openPlusMenu(page: import('@playwright/test').Page) {
  await page.goto('/', { waitUntil: 'networkidle' })
  const plus = page.locator('[data-promptbar] button').filter({ has: page.locator('svg') })
  for (const button of await plus.all()) {
    await button.click({ timeout: 2000 }).catch(() => {})
    if (await page.getByText('Sources', { exact: true }).isVisible().catch(() => false)) return
  }
  throw new Error('could not open the + menu')
}

test('the + menu Web search item does not insert a slash command', async ({ page }) => {
  await openPlusMenu(page)
  await page.getByRole('button', { name: 'Web search' }).click()

  const text = await composer(page).inputValue()
  expect(text, `composer was primed with ${JSON.stringify(text)}`).not.toMatch(/^\//)
  expect(text.toLowerCase()).toContain('search the web')
})

test('the + menu Memory recall item does not insert a slash command', async ({ page }) => {
  await openPlusMenu(page)
  await page.getByRole('button', { name: 'Memory recall' }).click()

  const text = await composer(page).inputValue()
  expect(text, `composer was primed with ${JSON.stringify(text)}`).not.toMatch(/^\//)
  expect(text.toLowerCase()).toContain('remember')
})

test('a command sent with an attachment is refused rather than silently dropping it', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' })

  await page.locator('[data-promptbar] input[type="file"]').first().setInputFiles({
    name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hello'),
  })
  await expect(page.getByText('notes.txt')).toBeVisible({ timeout: 15000 })
  // The filename appears immediately, before upload/extraction completes.
  // Sending during upload is deliberately ignored; exercise the ready-file
  // command guard instead of racing that separate protection.
  await expect(page.locator('[data-promptbar]').getByRole('status', { name: '' }).filter({ hasText: /^Ready$/ })).toBeVisible({ timeout: 30000 })

  await composer(page).fill('/tools')
  await composer(page).press('Enter')

  // The warning names the problem, and the file is still staged.
  await expect(page.getByRole('alert')).toContainText(/attachment/i)
  await expect(page.getByText('notes.txt')).toBeVisible()
})
