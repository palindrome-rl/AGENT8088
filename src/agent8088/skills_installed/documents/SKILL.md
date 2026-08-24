---
name: documents
description: Create and edit Word (.docx), Excel (.xlsx), PowerPoint (.pptx), and PDF files by writing Python via execute_shell; convert to PDF, read legacy .doc/.ppt/.xls, and recalculate formulas via LibreOffice when installed.
version: 1.1.0
category: software-development
---

No dedicated document tool exists. Write a Python script, run it with
`execute_shell` (`python script.py`), then verify with `read_text`.

Libraries already installed: `python-docx`, `openpyxl`, `python-pptx`,
`reportlab`. Do not install anything else.

**"Convert this file" means the existing file's actual content, not a fresh
one you write from scratch.** A conversion path exists — LibreOffice, see
below — use it. Writing a new script that generates a *different* document
with similar-sounding content is not a conversion and does not satisfy the
request, even if the script runs successfully.

**On Windows, `execute_shell` runs `cmd.exe`, not bash — for every command in
this skill, not just the LibreOffice ones.** `cmd.exe` only understands
double quotes; a leading/trailing `'single quote'` is not stripped and gets
passed to the program literally, which is why `python -c '...'` fails with a
syntax error. Use double quotes: `python -c "..."`. Prefer writing a `.py`
file with `write_file` and running `python script.py` over an inline `-c`
one-liner — one fewer layer of quoting to get wrong.

Always use an absolute output path. A bare filename gets redirected into
`artifacts/` by write-path rules — a later `read_text` on the bare name may
miss it.

## Always verify after writing

`read_text` extracts .docx/.xlsx/.pptx/.pdf to text automatically. Run it on
every file you just wrote. A script that "succeeds" can still produce a
corrupt or empty file — don't ship one unread.

```
read_text /absolute/path/to/output.docx
```

## Legacy formats (.doc / .xls / .ppt)

Never touch these with python-docx/openpyxl/python-pptx — they don't
understand the old binary format and you will produce a corrupt file.

LibreOffice, installed by the Windows installer alongside this agent, handles
them. See the LibreOffice section below to check it's actually present before
relying on it — the install can fail (no WinGet, offline machine) and degrades
to a warning rather than blocking setup, so don't assume it's there.

## Word (.docx) — python-docx

Create a new document:

```python
from docx import Document

doc = Document()
doc.add_heading("Report", level=0)
doc.add_heading("Summary", level=1)
doc.add_paragraph("This is a plain paragraph.")

p = doc.add_paragraph("A paragraph with a ")
p.add_run("bold word").bold = True

table = doc.add_table(rows=1, cols=2)
table.style = "Table Grid"
hdr = table.rows[0].cells
hdr[0].text = "Name"
hdr[1].text = "Value"
row = table.add_row().cells
row[0].text = "Widgets"
row[1].text = "42"

doc.save("/absolute/path/to/report.docx")
```

Editing an existing document is open → modify → save, never regenerate from
scratch — regenerating throws away formatting the user cares about.

```python
from docx import Document

doc = Document("/absolute/path/to/report.docx")
doc.add_paragraph("Appended paragraph.")
for p in doc.paragraphs:
    if p.text == "This is a plain paragraph.":
        p.text = "This paragraph was edited."
doc.save("/absolute/path/to/report.docx")
```

## Excel (.xlsx) — openpyxl

```python
from openpyxl import Workbook

wb = Workbook()
ws = wb.active
ws.title = "Data"
ws["A1"] = "Item"
ws["B1"] = "Price"
ws.append(["Widget", 9.99])
ws.append(["Gadget", 19.99])

ws["B4"] = "=SUM(B2:B3)"          # formula, for a human opening it in Excel
ws["B5"] = 29.98                   # computed number, for a script reading it back

ws["B2"].number_format = "#,##0.00"

ws2 = wb.create_sheet("Notes")
ws2["A1"] = "Second sheet"

wb.save("/absolute/path/to/data.xlsx")
```

**Formula trap**: openpyxl writes the formula string only — no cached
result. Reading a formula cell back with `load_workbook(path, data_only=True)`
returns `None`, not the number, until something recalculates it — either real
Excel, or LibreOffice headless (see below).

Rule: if a script or the agent itself needs the *value* immediately, compute
it in Python and write the number — that's simpler than a recalculation
round-trip. Reach for LibreOffice's recalc only when the file already has
formulas you didn't write yourself (e.g. one the user handed you) and you
need their computed values.

## PowerPoint (.pptx) — python-pptx

```python
from pptx import Presentation
from pptx.util import Inches

prs = Presentation()

title_layout = prs.slide_layouts[0]
slide = prs.slides.add_slide(title_layout)
slide.shapes.title.text = "Quarterly Update"
slide.placeholders[1].text = "Prepared by the team"

bullet_layout = prs.slide_layouts[1]
slide2 = prs.slides.add_slide(bullet_layout)
slide2.shapes.title.text = "Highlights"
body = slide2.placeholders[1].text_frame
body.text = "First point"
p = body.add_paragraph()
p.text = "Second point"
p.level = 1

slide2.notes_slide.notes_text_frame.text = "Remember to mention the budget."

prs.save("/absolute/path/to/deck.pptx")
```

Editing follows the same open → modify → save pattern:
`Presentation("/absolute/path/to/deck.pptx")`, change shapes/text, then
`.save()` back to the same path.

## LibreOffice (soffice) — conversion, legacy formats, formula recalc

The Windows installer tries to install LibreOffice via WinGet, but the install
can fail or be skipped (offline machine, no WinGet). Check before every use —
never assume it's there from a previous call.

**On Windows, `execute_shell` runs `cmd.exe`, not bash.** Use `dir`, not `ls`.
Use `2>nul`, not `2>/dev/null`. Do not chain with `&&`/`||` expecting POSIX
semantics. Getting this wrong wastes turns on `'ls' is not recognized` before
you ever reach the real work. The single most reliable check, which works
whether or not soffice is on PATH:

```
execute_shell: if exist "C:\Program Files\LibreOffice\program\soffice.exe" (echo FOUND) else (echo MISSING)
```

If that says MISSING, also try the `(x86)` path, then `where soffice`. If none
of them find it, tell the user LibreOffice isn't installed and point them at
https://www.libreoffice.org/download/ or rerunning the installer — don't
pretend the conversion happened.

**Never run a bare recursive listing** (`dir /s /b` on a project directory)
to find a file — it returns thousands of lines and buries the answer. Check
the exact path you were given instead.

**Converting .docx/.pptx/.xlsx to PDF.** `soffice` is usually NOT on PATH —
call it by full path, quoted (it contains a space):

```
execute_shell: "C:\Program Files\LibreOffice\program\soffice.exe" --headless --convert-to pdf --outdir "C:\out" "C:\in\report.docx"
```

The output filename is derived from the input, not chosen by you: `report.docx`
becomes `report.pdf` in `--outdir`. Verify it exists afterward rather than
assuming — soffice can print a conversion line and still produce nothing.

**Legacy .doc/.ppt/.xls → modern format**, then read it with `read_text` like
any other file — this feeds the existing extraction path, no separate reader
needed:

```
execute_shell: "C:\Program Files\LibreOffice\program\soffice.exe" --headless --convert-to docx --outdir "C:\out" "C:\in\old.doc"
read_text: C:\out\old.docx
```

**Recalculating formulas** in a file you didn't write yourself (e.g. one the
user handed you with existing formulas): convert it to itself to force a
recalc, giving the call its own isolated profile directory —

```
execute_shell: "C:\Program Files\LibreOffice\program\soffice.exe" --headless "-env:UserInstallation=file:///C:/temp/lo-profile-1" --convert-to xlsx --outdir "C:\out" "C:\in\data.xlsx"
```

Each call needs a **distinct** profile path (a fresh temp dir per call is
enough). Two `soffice` calls sharing one profile directory can collide and
fail unpredictably — this project doesn't run `soffice` inside a
network-restricted sandbox, so a fresh temp dir is enough; nothing heavier is
needed here.

**Cold start is slow** (3-5s) — well inside `execute_shell`'s default 25s
timeout, but don't assume the same speed as a Python library call.

## PDF — reportlab

Shortest section — PDF requests are rare. Use `SimpleDocTemplate` with a
list of flowables; reportlab lays out pagination for you.

```python
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, Spacer
from reportlab.lib.styles import getSampleStyleSheet

styles = getSampleStyleSheet()
doc = SimpleDocTemplate("/absolute/path/to/output.pdf", pagesize=letter)

story = [
    Paragraph("Report Title", styles["Title"]),
    Spacer(1, 12),
    Paragraph("This is body text.", styles["Normal"]),
    Spacer(1, 12),
    Table(
        [["Name", "Value"], ["Widgets", "42"]],
        style=[("GRID", (0, 0), (-1, -1), 0.5, colors.black)],
    ),
]

doc.build(story)
```

A PDF is not editable in place like docx/xlsx/pptx — there is no "open and
modify" for reportlab output. To change a generated PDF, regenerate it from
its source data.
