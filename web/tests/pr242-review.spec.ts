import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/history', route => route.fulfill({ json: { messages: [] } }))
  await page.addInitScript(() => {
    class Socket {
      static OPEN = 1
      readyState = 1
      onopen: any
      onmessage: any
      onclose: any
      constructor(url: string) {
        if (url.includes('/ws')) (window as any).__socket = this
        setTimeout(() => this.onopen?.({}), 0)
      }
      send(data: string) { (window as any).__sent = JSON.parse(data) }
      close() { this.readyState = 3 }
    }
    ;(window as any).WebSocket = Socket
  })
  await page.goto('/', { waitUntil: 'networkidle' })
  await page.waitForFunction(() => !!(window as any).__socket?.onmessage)
  await page.evaluate(() => (window as any).__socket.onmessage({ data: JSON.stringify({
    type: 'plan_approval', id: 'review-plan', plan: '# Proposed plan\n\n1. Read the project\n2. Apply the requested change',
  }) }))
})

test('plan card renders formatted text and sends the chosen mode', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Proposed plan' })).toBeVisible()
  await page.screenshot({ path: 'test-results/pr242-plan-card.png', fullPage: true })
  await page.getByRole('button', { name: 'Run in readonly' }).click()
  expect(await page.evaluate(() => (window as any).__sent)).toEqual({
    type: 'plan_approval', id: 'review-plan', mode: 'readonly',
  })
})

test('plan card stays available if the approval could not be delivered', async ({ page }) => {
  await page.evaluate(() => { (window as any).__socket.readyState = 3 })
  await page.getByRole('button', { name: 'Run in full-auto' }).click()
  await expect(page.getByText('Plan Proposal', { exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('Approval could not be sent')
  await page.getByRole('button', { name: 'Run in full-auto' }).click()
  await expect(page.getByText('Plan Proposal', { exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => (window as any).__sent)).toEqual({
    type: 'plan_approval', id: 'review-plan', mode: 'full-auto',
  })
})
