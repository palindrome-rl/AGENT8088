import { expect, test } from '@playwright/test'

/* The app's socket is a module-level singleton built with `new WebSocket(...)`.
 * Replacing window.WebSocket before any script runs lets these tests drive the
 * real reducers and the real components with no backend and no model. */
const STUB = () => {
  class FakeSocket {
    static OPEN = 1
    onopen: ((e: unknown) => void) | null = null
    onmessage: ((e: { data: string }) => void) | null = null
    onclose: ((e: unknown) => void) | null = null
    onerror: ((e: unknown) => void) | null = null
    readyState = 1
    constructor(public url: string) {
      // Vite also opens an HMR socket. Keep the application socket rather
      // than whichever WebSocket happened to be constructed last, otherwise
      // injected answer frames disappear into Vite after a live audit run.
      if (url.includes('/ws')) (window as any).__fake = this
      setTimeout(() => this.onopen?.({}), 0)
    }
    send() {}
    close() { this.readyState = 3 }
  }
  ;(window as any).WebSocket = FakeSocket as unknown as typeof WebSocket
  ;(window as any).__push = (event: unknown) =>
    (window as any).__fake?.onmessage?.({ data: JSON.stringify(event) })
}

const DIFF = {
  file: 'src/app.py', added: 1, removed: 1, truncated: false,
  lines: [
    { text: 'def run(x):', tone: 'ctx' },
    { text: '    return x', tone: 'del' },
    { text: '    return x * 2', tone: 'add' },
  ],
}

async function push(page: any, event: unknown) {
  await page.evaluate((e: unknown) => (window as any).__push(e), event)
}

/** Get the app into its streaming state, which is when tool activity renders. */
async function streaming(page: any) {
  await page.addInitScript(STUB)
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.waitForFunction(() => !!(window as any).__fake)
  await push(page, { type: 'spin', message: 'running edit_file...', elapsed: 1, tokens: 0 })
}

test('a write shows a diff chip under a divider, below the tool rows', async ({ page }) => {
  await streaming(page)
  await push(page, { type: 'tool_start', name: 'edit_file' })
  await push(page, {
    type: 'tool_result', name: 'edit_file',
    result: 'Updated src/app.py with 1 addition and 1 removal', diff: DIFF,
  })

  const strip = page.getByTestId('tool-diff-strip')
  await expect(strip).toBeVisible()
  await expect(strip).toContainText('src/app.py')
  await expect(strip).toContainText('+1')
  await expect(strip).toContainText('−1')

  // The strip summarises the run, so it sits after the rows.
  const rows = page.getByTestId('tool-rows')
  const rowsBox = await rows.boundingBox()
  const stripBox = await strip.boundingBox()
  expect(stripBox!.y).toBeGreaterThan(rowsBox!.y)
})

test('hovering a chip opens the diff preview with added and removed lines', async ({ page }) => {
  await streaming(page)
  await push(page, { type: 'tool_start', name: 'edit_file' })
  await push(page, {
    type: 'tool_result', name: 'edit_file',
    result: 'Updated src/app.py with 1 addition and 1 removal', diff: DIFF,
  })

  await page.getByRole('button', { name: 'Show diff for src/app.py' }).hover()
  const preview = page.getByTestId('tool-diff-preview')
  await expect(preview).toBeVisible()
  await expect(preview).toContainText('return x * 2')
  await expect(preview).toContainText('def run(x):')
})

test('a tool that wrote nothing shows no strip', async ({ page }) => {
  await streaming(page)
  await push(page, { type: 'tool_start', name: 'web_search' })
  await push(page, { type: 'tool_result', name: 'web_search', result: '3 results' })
  await expect(page.getByTestId('tool-rows')).toBeVisible()
  await expect(page.getByTestId('tool-diff-strip')).toHaveCount(0)
})

test('review progress and readable findings do not create a write diff', async ({ page }) => {
  await streaming(page)
  await push(page, { type: 'tool_start', name: 'review_code' })
  await expect(page.getByTestId('tool-rows')).toContainText('review_code')
  await push(page, { type: 'tool_result', name: 'review_code', result:
    'Code review: complete\nhigh: discount.py:3 — Incorrect percentage\nStored review: rev-test' })
  const rows = page.getByTestId('tool-rows')
  await expect(rows).toContainText('Code review: complete')
  await expect(page.getByTestId('tool-diff-strip')).toHaveCount(0)
})

test('long review names stay contained without crushing short columns', async ({ page }) => {
  await streaming(page)
  await push(page, {
    type: 'answer',
    text: '| File | Change |\n|---|---|\n| `tests/test_engine_playwright_browsers_path.py` | New `test_exec_browser_pins_asyncio_logger_before_the_chromium_probe` verifies the call order |',
  })
  await push(page, { type: 'turn_complete' })

  const markdown = page.locator('.chat-markdown').last()
  await expect(markdown.getByRole('table')).toBeVisible()
  const clipped = await markdown.evaluate((element) => element.scrollWidth > element.clientWidth)
  expect(clipped).toBe(false)
  await expect(markdown.locator('td code').last()).toHaveCSS('overflow-wrap', 'anywhere')
  const firstCell = markdown.locator('td').first()
  expect(await firstCell.evaluate((element) => element.getBoundingClientRect().width)).toBeGreaterThan(100)
})

test('two writes to the same file collapse into one chip with combined counts', async ({ page }) => {
  await streaming(page)
  for (const [added, removed] of [[1, 1], [3, 2]]) {
    await push(page, { type: 'tool_start', name: 'edit_file' })
    await push(page, {
      type: 'tool_result', name: 'edit_file', result: 'Updated src/app.py',
      diff: { ...DIFF, added, removed },
    })
  }
  const chips = page.getByRole('button', { name: /^Show diff for/ })
  await expect(chips).toHaveCount(1)
  await expect(page.getByTestId('tool-diff-strip')).toContainText('+4')
  await expect(page.getByTestId('tool-diff-strip')).toContainText('−3')
})
