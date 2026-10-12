import { expect, test } from '@playwright/test'

const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3x8AAAAASUVORK5CYII=', 'base64')

test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    const json = path === '/api/status' ? { session_name: 'image-review', permission_mode: 'full-auto' }
      : path === '/api/history' ? { messages: [] }
      : path === '/api/providers' ? { configured: [], builtins: [], active: '', details: {} }
      : path === '/api/attachments' ? { id: 'a'.repeat(32), status: 'ready', warning: '' }
      : []
    return route.fulfill({ json })
  })
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
})

test('image analysis picker attaches and sends an image with the question', async ({ page }) => {
  await page.getByRole('button', { name: 'Add attachments and sources' }).click()
  const chooser = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: 'Image analysis' }).click()
  await (await chooser).setFiles({ name: 'screenshot.png', mimeType: 'image/png', buffer: pixel })
  await expect(page.getByRole('button', { name: 'Remove screenshot.png' })).toBeVisible()
  await page.getByRole('textbox', { name: 'Prompt' }).fill('What does this screenshot show?')
  await expect(page.locator('[data-promptbar]')).toContainText('Ready')
  await page.screenshot({ path: 'test-results/pr243-image-picker.png', fullPage: true })
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as any).__sent)).toEqual({
    type: 'chat', text: 'What does this screenshot show?', attachments: ['a'.repeat(32)],
  })
})

test('pasted screenshots attach, but ordinary clipboard text remains text', async ({ page }) => {
  await page.getByRole('textbox', { name: 'Prompt' }).evaluate(input => {
    const data = new DataTransfer()
    data.items.add(new File(['image'], 'pasted.png', { type: 'image/png' }))
    input.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }))
  })
  await expect(page.getByRole('button', { name: 'Remove pasted.png' })).toBeVisible()
  const prevented = await page.getByRole('textbox', { name: 'Prompt' }).evaluate(input => {
    const data = new DataTransfer()
    data.setData('text/plain', 'copied text')
    data.items.add(new File(['image'], 'office.png', { type: 'image/png' }))
    const event = new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true })
    input.dispatchEvent(event)
    return event.defaultPrevented
  })
  expect(prevented).toBe(false)
  await expect(page.getByRole('button', { name: 'Remove office.png' })).toHaveCount(0)
})

test('dropping an image attaches it', async ({ page }) => {
  await page.getByRole('textbox', { name: 'Prompt' }).evaluate(input => {
    const data = new DataTransfer()
    data.items.add(new File(['image'], 'dropped.png', { type: 'image/png' }))
    input.dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }))
  })
  await expect(page.getByRole('button', { name: 'Remove dropped.png' })).toBeVisible()
})

test('blocked clipboard access explains how to recover', async ({ page }) => {
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {
    configurable: true, value: { read: () => Promise.reject(new Error('denied')) },
  }))
  await page.getByRole('button', { name: 'Paste image from clipboard' }).click()
  await expect(page.getByRole('alert')).toContainText('Press Ctrl+V')
})

test('clipboard button attaches the browser clipboard image', async ({ page }) => {
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {
    configurable: true, value: { read: async () => [{
      types: ['image/png'], getType: async () => new Blob(['image'], { type: 'image/png' }),
    }] },
  }))
  await page.getByRole('button', { name: 'Paste image from clipboard' }).click()
  await expect(page.locator('[data-promptbar]')).toContainText('Ready')
  await expect(page.getByRole('button', { name: /^Remove clipboard-.*\.png$/ })).toBeVisible()
})

test('empty clipboard explains how to attach an image', async ({ page }) => {
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {
    configurable: true, value: { read: async () => [] },
  }))
  await page.getByRole('button', { name: 'Paste image from clipboard' }).click()
  await expect(page.getByRole('alert')).toContainText('No image on the clipboard')
})

test('upload failure stays visible and cannot silently send an empty message', async ({ page }) => {
  await page.route('**/api/attachments', route => route.fulfill({ status: 413, json: { error: 'File is too large' } }))
  await page.locator('input[type="file"]').first().setInputFiles({ name: 'large.png', mimeType: 'image/png', buffer: pixel })
  await expect(page.locator('[data-promptbar]')).toContainText('Upload failed')
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toHaveAttribute('title', 'File is too large')
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled()
})
