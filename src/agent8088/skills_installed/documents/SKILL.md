---
name: documents
description: Create and edit Word (.docx), Excel (.xlsx), PowerPoint (.pptx), and PDF files by writing Python and running it via execute_shell.
version: 1.0.0
category: software-development
---

No dedicated document tool exists. Write a Python script, run it with
`execute_shell` (`python script.py`), then verify with `read_text`.

Libraries already installed: `python-docx`, `openpyxl`, `python-pptx`,
`reportlab`. Do not install anything else.

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

## Legacy formats are not supported

`.doc`, `.xls`, `.ppt` need LibreOffice, which is not a dependency here. Do
not attempt to write or edit them with these libraries — you will produce a
corrupt file. Tell the user legacy formats aren't supported and offer the
modern equivalent (.docx/.xlsx/.pptx) instead.

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
result. There is no LibreOffice in this repo to recalculate it. Reading a
formula cell back with `load_workbook(path, data_only=True)` returns `None`,
not the number, until the file has been opened and saved in real Excel.

Rule: if a script or the agent itself needs the *value*, compute it in
Python and write the number. Write a formula only when a human will open the
file in Excel and expects to see live formulas.

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
