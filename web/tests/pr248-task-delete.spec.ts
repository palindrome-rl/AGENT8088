import { expect, test } from '@playwright/test'

test('ending a queued task refreshes its status and enables deletion without an API error', async ({ page }) => {
  let task: Record<string, unknown> | null = {
    id: 'test-task-248', goal: 'My queued report', state: 'queued', slice_no: 0,
    worker_active: false, last_answer: 'Saved checkpoint', error: '', operations: [],
  }
  await page.route('**/api/tasks**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === 'DELETE') {
      task = null
      return route.fulfill({ json: { ok: true } })
    }
    if (path.endsWith('/end')) task = { ...task, state: 'cancelled', error: 'ended by user' }
    return route.fulfill({ json: path === '/api/tasks' ? (task ? [task] : []) : task })
  })
  await page.goto('/tasks')
  await page.getByRole('button', { name: /My queued report/ }).click()
  await expect(page.getByRole('button', { name: 'Delete', exact: true })).toBeDisabled()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: 'End', exact: true }).click()
  await expect(page.getByRole('button', { name: 'End', exact: true })).toHaveCount(0)
  await expect(page.getByText('ended by user', { exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Delete', exact: true })).toBeEnabled()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: 'Delete', exact: true }).click()
  await expect(page.getByRole('button', { name: /My queued report/ })).toHaveCount(0)
  await expect(page.getByText('No durable tasks yet.')).toBeVisible()
})
