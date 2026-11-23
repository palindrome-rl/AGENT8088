"""Reference solution (used only to validate the checker). usage: python make_outputs.py <target folder>"""
import csv, json, os, sys
from datetime import datetime
from pathlib import Path
import openpyxl
from openpyxl.chart import BarChart, Reference
from pptx import Presentation
from pptx.util import Inches
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

here = Path(__file__).resolve().parents[3] / "bench" / "tasks" / "qbr-review-pack"
fx = here / "fixture"
exp = json.load(open(here / "expected.json", encoding="utf-8"))
target = Path(sys.argv[1]) / "outputs"
target.mkdir(parents=True, exist_ok=True)
rate = {"USD": 1.0, "EUR": 1.10}
rows, seen = [], set()
for f in sorted(fx.glob("sales_*.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8")):
        if r["order_id"] in seen:
            continue
        seen.add(r["order_id"])
        d = r["date"]
        dt = datetime.strptime(d, "%m/%d/%Y") if "/" in d else datetime.strptime(d, "%Y-%m-%d")
        q, p = int(r["quantity"]), float(r["unit_price"])
        rows.append([dt.strftime("%Y-%m-%d"), r["order_id"], r["region"], r["product"], q, p, r["currency"], round(q * p * rate[r["currency"]], 2)])
wb = openpyxl.Workbook()
data = wb.active; data.title = "Data"
data.append(["date", "order_id", "region", "product", "quantity", "unit_price", "currency", "revenue_usd"])
for r in rows:
    data.append(r)
n = len(rows) + 1
sm = wb.create_sheet("Summary")
sm.append(["Q3 revenue (USD)", f"=SUM(Data!H2:H{n})"])
sm.append(["Budget (USD)", exp["budget_total"]])
sm.append(["Variance (USD)", "=B1-B2"])
sm.append(["Variance %", "=ROUND(100*B3/B2,1)"])
sm.append(["Supplier cost (USD)", exp["supplier_cost"]])
sm.append([])
sm.append(["Month", "Revenue"])
for i, m in enumerate(("07", "08", "09")):
    sm.append([f"2026-{m}", f'=SUMPRODUCT((MID(Data!A2:A{n},6,2)="{m}")*Data!H2:H{n})'])
sm.append([])
sm.append(["Region", "Revenue"])
for rg in ("North", "South", "East", "West"):
    sm.append([rg, f'=SUMIF(Data!C2:C{n},"{rg}",Data!H2:H{n})'])
ch = BarChart(); ch.add_data(Reference(sm, min_col=2, min_row=7, max_row=10), titles_from_data=True)
ch.set_categories(Reference(sm, min_col=1, min_row=8, max_row=10)); sm.add_chart(ch, "D2")
iss = wb.create_sheet("Issues")
iss.append(["issue", "action"])
iss.append([f"Repeated orders {', '.join(exp['duplicate_order_ids'])}", "counted once"])
iss.append(["INV-004 is a duplicate of INV-002", "counted once"])
iss.append(["INV-005: PDF 4,820.00 vs register 4,280.00", "used the PDF amount"])
iss.append(["INV-006 is an unreadable scan", "not processed, left out of the cost total"])
wb.save(target / "qbr_workbook.xlsx")

prs = Presentation()
def slide(title, body):
    s = prs.slides.add_slide(prs.slide_layouts[1]); s.shapes.title.text = title; s.placeholders[1].text = body
t, b, v, c = exp["revenue_total"], exp["budget_total"], exp["variance"], exp["supplier_cost"]
slide("Q3 2026 review", "Harbor Supply Co.")
slide("Headline numbers", f"Revenue ${t:,.2f}\nBudget ${b:,.2f}\nVariance ${v:,.2f} ({exp['variance_pct']}%)\nSupplier cost ${c:,.2f}")
slide("Revenue by month", "\n".join(f"{k}: ${x:,.2f}" for k, x in exp["revenue_by_month"].items()))
slide("Revenue by region", "\n".join(f"{k}: ${x:,.2f}" for k, x in exp["revenue_by_region"].items()))
slide("Issues found", f"Repeated orders {', '.join(exp['duplicate_order_ids'])}\nINV-004 duplicate\nINV-005 amount mismatch\nINV-006 unreadable scan")
slide("Next steps", "Resolve invoice issues\nReview budget")
prs.save(target / "qbr_deck.pptx")

cv = canvas.Canvas(str(target / "qbr_memo.pdf"), pagesize=A4)
y = 800
for line in ["Q3 2026 memo", f"Revenue ${t:,.2f}  Budget ${b:,.2f}  Variance ${v:,.2f} ({exp['variance_pct']}%)", f"Supplier cost ${c:,.2f}",
             f"Issues: repeated orders {', '.join(exp['duplicate_order_ids'])}; INV-004 duplicate; INV-005 mismatch; INV-006 unreadable"]:
    cv.drawString(50, y, line); y -= 24
cv.save()
print("reference outputs written to", target)
