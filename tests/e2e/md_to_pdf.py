#!/usr/bin/env python3
"""Render the E2E run summary markdown to a PDF via Playwright (already in the image).

Reads /workspace/logs/run_summary.md, converts markdown -> HTML with a minimal
styled template, and prints to /workspace/logs/Agent8088_E2E_Report.pdf.

No new dependencies: markdown is a tiny pure-python table/heading/code renderer
(good enough for this report); Playwright+Chromium is already installed.
"""
import html
import re
import sys
from pathlib import Path

SRC = Path("/workspace/logs/run_summary.md")
OUT = Path("/workspace/logs/Agent8088_E2E_Report.pdf")


def md_to_html(md: str) -> str:
    """Tiny markdown subset renderer: headings, tables, bold, code, lists, hr, paragraphs.

    ponytail: a full markdown parser is not worth a dependency for one report.
    Handles exactly the constructs this summary uses.
    """
    lines = md.splitlines()
    out = []
    i = 0
    in_list = False

    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`([^`]+)`", r'<code>\1</code>', s)
        return s

    while i < len(lines):
        line = lines[i]
        # Headings
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue
        # Horizontal rule
        if re.match(r"^-{3,}\s*$", line):
            out.append("<hr/>")
            i += 1
            continue
        # Table (header | sep | rows)
        if "|" in line and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = ['<table><thead><tr>'] + [f"<th>{inline(h)}</th>" for h in header] + ['</tr></thead><tbody>']
            for r in rows:
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue
        # Fenced code block
        if line.strip().startswith("```"):
            lang = line.strip().strip("`")
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append(f'<pre><code class="lang-{lang}">{html.escape(chr(10).join(code))}</code></pre>')
            continue
        # Unordered list
        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(m.group(1))}</li>")
            i += 1
            continue
        elif in_list:
            out.append("</ul>")
            in_list = False
        # Blank line
        if not line.strip():
            i += 1
            continue
        # Paragraph (consume consecutive non-blank lines)
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|\|)", lines[i]) and not lines[i].strip().startswith("```") and not re.match(r"^\s*[-*]\s", lines[i]):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


CSS = """
@page { margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
       font-size: 10.5pt; line-height: 1.5; color: #1a1a1a; max-width: 100%; }
h1 { font-size: 22pt; color: #0a2540; border-bottom: 3px solid #0077B6; padding-bottom: 6px; margin: 0 0 4pt; }
h2 { font-size: 14pt; color: #0077B6; margin: 18pt 0 6pt; border-bottom: 1px solid #cfe; padding-bottom: 3px; }
h3 { font-size: 11.5pt; color: #237dd7; margin: 12pt 0 4pt; }
p { margin: 4pt 0; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.5pt; }
th, td { border: 1px solid #cdd; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #0077B6; color: #fff; font-weight: 600; }
tr:nth-child(even) td { background: #f4f8fb; }
code { background: #eef2f5; padding: 1px 4px; border-radius: 3px; font-family: 'Consolas','SF Mono',monospace; font-size: 9pt; color: #b10058; }
pre { background: #0d1b2a; color: #e6e6e6; padding: 10px 12px; border-radius: 6px; overflow-x: auto; font-size: 8.5pt; }
pre code { background: none; color: inherit; padding: 0; }
hr { border: none; border-top: 1px solid #cdd; margin: 14pt 0; }
ul { margin: 4pt 0; padding-left: 20px; }
li { margin: 2pt 0; }
strong { color: #0a2540; }
.banner { background: #0077B6; color: #fff; padding: 10px 14px; border-radius: 6px; margin-bottom: 12pt; }
.banner h1 { color: #fff; border: none; margin: 0; font-size: 18pt; }
.banner .sub { font-size: 9.5pt; opacity: 0.9; margin-top: 2pt; }
.pass { color: #0a7f3a; font-weight: 700; }
.fail { color: #c0392b; font-weight: 700; }
"""

def main():
    md = SRC.read_text(encoding="utf-8")
    body = md_to_html(md)
    # Colorize PASS/FAIL tokens in the table
    body = body.replace(">PASS<", '><span class="pass">PASS</span><')
    body = body.replace(">FAIL<", '><span class="fail">FAIL</span><')
    full = f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
<div class="banner"><h1>Agent8088 — End-to-End Test Report</h1>
<div class="sub">Docker-based E2E against ornith-1.0-35b @ http://127.0.0.1:8080/v1</div></div>
{body}
</body></html>"""
    html_path = OUT.with_suffix(".html")
    html_path.write_text(full, encoding="utf-8")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"file://{html_path}")
        page.wait_for_load_state("networkidle")
        page.pdf(path=str(OUT), format="A4", print_background=True,
                 margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"})
        browser.close()
    print(f"PDF written: {OUT} ({OUT.stat().st_size} bytes)")
    html_path.unlink()  # keep only the PDF

if __name__ == "__main__":
    main()