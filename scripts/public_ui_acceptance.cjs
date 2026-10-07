// Run against an isolated, already-started source Web UI; makes no model calls.
// Build first: npm ci --prefix web && npm run build --prefix web
// Then: node scripts/public_ui_acceptance.cjs
const { chromium } = require('../web/node_modules/@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

(async () => {
  const base = process.env.AGENT8088_UI_TEST_URL || 'http://127.0.0.1:8874';
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'agent8088-public-ui-'));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const status = await page.request.get(`${base}/api/status`);
    assert.equal(status.status(), 200);
    assert.ok(Array.isArray((await status.json()).capabilities));
    for (const route of ['/', '/tools', '/config', '/doctor']) {
      await page.goto(base + route, { waitUntil: 'networkidle', timeout: 120000 });
      await page.screenshot({ path: path.join(output, `${route.slice(1) || 'chat'}-desktop.png`), fullPage: true });
      assert.ok((await page.locator('body').innerText()).length > 100, `empty ${route}`);
    }
    const badge = page.getByRole('button', { name: /capabilities limited/ });
    if (await badge.count()) {
      await badge.click();
      await page.getByText('Running in a reduced mode').waitFor();
      await page.screenshot({ path: path.join(output, 'limited-capabilities.png'), fullPage: true });
      await page.keyboard.press('Escape');
      assert.equal(await page.getByText('Running in a reduced mode').count(), 0);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${base}/doctor`, { waitUntil: 'networkidle', timeout: 120000 });
    await page.screenshot({ path: path.join(output, 'doctor-mobile.png'), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false,
      'Doctor page overflows the mobile viewport');
    assert.equal(await page.locator('table').evaluateAll(tables => tables.some(table => table.scrollWidth > table.clientWidth + 1)), false,
      'Doctor health checks are clipped inside their container');
    assert.deepEqual(errors, [], 'browser JavaScript errors');
    console.log(`UI acceptance passed; screenshots: ${output}`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
