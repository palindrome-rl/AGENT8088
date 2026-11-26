---
name: browser-qa
description: Verify anything you built that has a visible or interactive side (a page, a form, a chart, a layout, a canvas app, a drawing or image file) by loading it, exercising its main action and looking at a real render. Use before calling such work done, and whenever the user asks whether it looks or works right, including on a phone-sized screen.
version: 1.0
progressive: true
requires_tools: screenshot_page
---

# Browser QA

Check visual and interactive work against what was asked, with evidence from the real file, not
from memory of what you wrote. This skill is about verifying. For how to build a good interface,
see `frontend-ui-engineering`.

## Which tool answers which question

| Question | Tool |
|---|---|
| Does it load and run without errors? | `check_app` (pages and scripts) |
| Does the main action work? | `check_app` (clicks the first button); for an app running as a service, `browser_test` |
| What is on the page, and where? | `browser_test` with an `inspect` step |
| Does a game or a canvas respond to keys and clicks? | `browser_test` with mouse and key steps, `expect_js`, screenshots |
| Does it look right? | `screenshot_page`, then look at the image it attaches |
| What does an existing image file show? | `view_image` |
| Fix a problem you found | `edit_file`, a small targeted change |

## 1. It loads without errors

Run `check_app` on the page. Fix every page error, missing file and failing control it reports
before judging how anything looks: a page that throws can render half its content.

## 2. The main action works

When the work has an action (submitting a form, filtering a table, a button that draws or
updates something), prove it instead of assuming it.

Start with `check_app`: it clicks the first button and reports one that does nothing or throws.
Then render with `screenshot_page` and confirm the starting state is what the action expects.

For an app you run with `service_start`, use `browser_test` (section 2a). For a page from files,
a short headless test script can drive the page. It needs a
browser inside the sandbox, which not every setup can start: the Windows sandbox, for one, cannot
start a browser (it fails with `spawn EPERM` or a crash at launch). If the browser cannot start, do
not retry, install anything or switch tools to get around it. Say that the action was not verified
beyond what `check_app` showed.

When a browser can start, run a script like this with `execute_shell`:

```python
# test_signup.py: run with  python -m pytest -q test_signup.py
from pathlib import Path
from playwright.sync_api import sync_playwright

def test_submit_shows_confirmation():
    page_url = Path("signup.html").resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(page_url)
        page.fill("#email", "someone@example.com")
        page.click("text=Create account")
        page.wait_for_selector("#confirmation")          # wait for the result, never sleep
        assert "Thanks" in page.inner_text("#confirmation")
        browser.close()
```

- Headless, and the file loaded from disk: no servers you did not need.
- Stable selectors: ids, labels or visible text, not deep CSS paths.
- Wait for a selector or a state, never a fixed sleep.
- Keep the script next to the work so the user can run it again.

## 2a. Test a running app like a user: plan, act, look, check

`browser_test` drives the app in a real browser on this computer, so it works where the sandbox
cannot start one. Work in short cycles:

1. **Look first.** Run a test of one step, `[{"inspect": true}]`. It lists every visible button,
   field, link and canvas with its position, size and a selector, and attaches a screenshot. Plan
   from that list and the picture, not from memory of the code.
2. **Plan the steps.** Write down what a user would do and what they should then see: "press
   ArrowRight three times, the player moves right; click the canvas at the coin, the score goes up".
   Each "should see" becomes an `expect_*` step.
3. **Act.**
   - Forms and buttons: `click`, `fill`, `select`, `press` with selectors.
   - A canvas, a game, a drawing board: `click_at [x, y]` with `"selector": "canvas"` so the point is
     inside the canvas, `drag` with `to`, `hover`.
   - Keys: `press` (with `times`), `hold` with `ms` for movement, `keydown` / `keyup` for combinations.
     Many games listen on the document; if keys do nothing, `click_at` the canvas first to focus it.
   - A phone-sized screen: `viewport [375, 740]`.
4. **Check.**
   - Text and elements: `expect_text`, `expect_visible`, `expect_count`.
   - State a canvas draws from: `expect_js` with an expression, for example
     `"game.score === 1"` or `"document.querySelector('#board').dataset.turn === 'O'"`. Read a value
     with a `js` step first if you do not know its name.
   - Pixels: `expect_changed` with the canvas as selector after an action that should redraw it, and
     `expect_unchanged` after one that should not (pause, a wall). An animated page always changes,
     so check its state with `expect_js` instead.
5. **Look.** Add `screenshot` steps at the moments that matter ("after the jump", "game over").
   The images are attached to the result: look at each one and say what it shows. With `"grid": true`
   the picture carries a labelled coordinate grid, for choosing the next `click_at` point. Coordinates
   are page pixels as reported in the text, not the size of the attached image.

When a step fails, the result says what the page showed and attaches the state it stopped in. Fix
the app with a small change, restart the service if it serves built files, and run the same test
again. A test without `expect_*` steps only shows that the steps ran.

## 2b. Make interactive work testable: the test seam

A game or any app driven by clicks, keys and timers is hard to check from outside: the state lives
in variables, and real timers make a test wait or guess. When you build one, add a small test seam
while you write it, the same way every time:

- **A read-only state function.** `window.__test = { state: () => ({ score, lives, player: {x, y},
  turn, over }) }`: a plain object of what a person would judge the app by. A test reads it with
  `expect_js`, for example `"window.__test.state().score === 1"`.
- **An optional seed.** Read `?seed=N` from the URL and use it for every random choice (a small
  seeded generator), so a test can load the same board, deck or level every time. Without a seed
  the app stays random as before.
- **Optional, for timed games:** `window.__test.step(ms)` advances the game by that much time
  without waiting on real timers.

The seam must not change normal play: no visible controls, no extra output, the same behaviour when
nobody calls it. Then play-test each interaction the request asks for with `browser_test` (section
2a). An interactive part of the request counts as checked only after a passing play test since the
last change; a unit test of a helper or loading the page does not show the interaction works.

## 3. It looks right

Render it with `screenshot_page` and look at the attached image. Then compare it with the request,
point by point, and name what you see:

- **A form page:** every field and label present, aligned, nothing clipped or overlapping.
- **A chart:** the right number of bars or points, axis labels readable, values in the right order.
- **A layout:** sections in the requested order, spacing consistent, nothing running off the edge.
- **A drawing or image file:** the shapes asked for are there, in the right place and proportion.
- **A canvas app:** something is actually drawn, not an empty frame.

Read the evidence block beside the image as well:

- `looks blank`: nothing was drawn. Check the file paths, the script and the sizes before anything else.
- **Page or console errors:** fix those first.
- `not well formed` (for XML-based files such as SVG): fix the markup at the reported line.
- **The change from the previous render:** after a fix, it confirms the file really changed. If it says
  identical, your edit did not take effect.
- **Still changing on screen:** the page animates, so judge the picture, not the pixel difference.

When something is wrong, say exactly what (for example, "the third label overlaps the bar") and fix
it with a small `edit_file` change rather than rewriting the file. Render again to confirm the fix.

## 4. Edge cases

- **A narrow screen:** render with `width` 375 and `height` 740 and check that nothing overflows, overlaps
  or hides a control.
- **Longer content:** a long name, many items, an empty list.
- **A full page:** pass `full_page` when the content is taller than the window.

## Reporting

Say what you checked and how: which renders you looked at, what the test script asserted, and anything
you could not verify. For example, when the browser is unavailable, say plainly that how the work looks
was not verified, and do not describe a picture you never saw.
