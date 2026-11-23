"""Reference solution through the ERP's HTML forms (validates the server and the checker)."""
import http.cookiejar, json, re, sys, urllib.parse, urllib.request
from pathlib import Path
import openpyxl
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

here = Path(__file__).resolve().parents[2].parent / "bench" / "tasks" / "expense-erp"
exp = json.load(open(here / "expected.json", encoding="utf-8"))
out = Path(sys.argv[1]) / "outputs"; out.mkdir(parents=True, exist_ok=True)
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
base = "http://127.0.0.1:8910"


def post(path, **data):
    return op.open(base + path, urllib.parse.urlencode(data).encode()).read().decode()


post("/login", username="clerk", password="harbor-demo-1")
ids = {}
for rid, info in {**exp["submit"], **exp["draft"]}.items():
    html = post("/expenses/new", date=info["date"], vendor=info["vendor"], category=info["category"], amount_usd=info["usd"], receipt_ref=rid, note="")
    ids[rid] = int(re.search(r"Expense (\d+)", html).group(1))
for rid in exp["draft"]:
    post(f"/expenses/{ids[rid]}/comment", comment=f"{exp['draft'][rid]['category']} limit exceeded by this {exp['draft'][rid]['usd']:.2f} USD expense")
for rid in exp["submit"]:
    post(f"/expenses/{ids[rid]}/submit"); post(f"/expenses/{ids[rid]}/confirm")
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Exceptions"
ws.append(["receipt", "issue", "detail", "amount_usd"])
for rid, info in exp["draft"].items():
    ws.append([rid, "policy limit", "above the limit", info["usd"]])
ws.append(["R-07", "duplicate", "same purchase as R-01", 32.0]); ws.append(["R-15", "unreadable", "scanned receipt", None]); ws.append(["R-09", "statement mismatch", "statement 102.00, receipt 120.00", 120.0])
wb.save(out / "exceptions.xlsx")
c = canvas.Canvas(str(out / "summary.pdf"), pagesize=A4)
c.drawString(60, 780, f"Submitted 8 expenses, total ${exp['submitted_total']:,.2f}; 5 drafts (policy); exceptions: R-07 R-09 R-15")
c.save()
print("reference done; ids", ids)
