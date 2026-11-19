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
| Does the main action work? | a small headless test script, run with `execute_shell` |
| Does it look right? | `screenshot_page`, then look at the image it attaches |
| What does an existing image file show? | `view_image` |
| Fix a problem you found | `edit_file`, a small targeted change |

## 1. It loads without errors

Run `check_app` on the page. Fix every page error, missing file and failing control it reports
before judging how anything looks: a page that throws can render half its content.

## 2. The main action works

When the work has an action (submitting a form, filtering a table, a button that draws or
updates something), prove it with a short headless script instead of assuming it:

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
