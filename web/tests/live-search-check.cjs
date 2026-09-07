// Explicit opt-in smoke test against a running, configured agent (uses model quota).
// Run from web/: node tests/live-search-check.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ baseURL: process.env.AGENT8088_WEB_URL || 'http://127.0.0.1:8181', viewport: { width: 1280, height: 900 } });
    const events = [];
    page.on('websocket', socket => socket.on('framereceived', frame => {
      try { events.push(JSON.parse(frame.payload.toString())); } catch {}
    }));
    await page.goto(process.env.AGENT8088_WEB_URL || 'http://127.0.0.1:8181');
    await page.waitForTimeout(1000);
    for (const [index, query] of (process.env.AGENT8088_CHECK_QUERY ? [process.env.AGENT8088_CHECK_QUERY] : [
      'can you tell me the scores of saturday nights premier league games?',
      'Search for the latest stable Python release. Give its version, release date and source link.',
      'Search for the latest stable Firefox release and its release date. Cite the source.'
    ]).entries()) {
      await page.getByRole('button', { name: 'New chat', exact: true }).click();
      await page.waitForTimeout(700);
      const before = await page.request.get('/api/status').then(r => r.json());
      events.length = 0;
      await page.locator('textarea').fill(query);
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      await page.locator('[role="status"]').waitFor({ state: 'visible' });
      await page.waitForTimeout(400);
      await page.screenshot({ path: `test-results/search-${index}-working.png` });
      const deadline = Date.now() + 180000;
      while (!events.some(e => ['answer', 'error'].includes(e.type)) && Date.now() < deadline) {
        await page.waitForTimeout(1000);
      }
      const answer = events.find(e => e.type === 'answer');
      assert(answer, 'No completed answer');
      const tools = events.filter(e => e.type === 'tool_start').map(e => e.name);
      assert(tools.includes('web_search'), 'Expected search');
      assert(!tools.includes('browse_page'), 'Unexpected browser escalation');
      console.log(JSON.stringify({ query, tools, usage: answer.usage, answer: answer.text }));
      await page.waitForTimeout(500);
      await page.screenshot({ path: `test-results/search-${index}-answer.png` });
      assert(!await page.locator('[role="status"]').count(), 'Working indicator remained after completion');
      const cells = await page.locator('td').evaluateAll(es => es.map(e => getComputedStyle(e).padding));
      assert(cells.every(p => p !== '0px'), 'Unstyled table cells');
      await page.reload();
      await page.waitForTimeout(1000);
      assert((await page.locator('body').innerText()).includes(query), 'History did not restore');
      const after = await page.request.get('/api/status').then(r => r.json());
      assert.equal(after.session_name, before.session_name);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
