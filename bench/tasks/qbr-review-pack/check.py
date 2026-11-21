"""Checker for the Q3 review pack task.

usage: python check.py <folder that contains outputs/> [--json out.json]
Ten scenarios worth 6 points each (60 total). Reads the workbook, deck and memo the agent produced.
"""
import json, os, re, sys
from pathlib import Path
import openpyxl
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
EXP = json.load(open(HERE / "expected.json", encoding="utf-8"))


def find_outputs(folder):
    folder = Path(folder)
    cands = [c for c in [folder / "outputs", folder / "artifacts" / "outputs"] + sorted(folder.rglob("outputs")) if c.is_dir()]
    full = [c for c in cands if any(p.is_file() for p in c.rglob("*"))]   # an empty outputs/ is not the deliverable
    if len(full) > 1:           # deliverables split between the project and the artifacts folder: judge them together
        import shutil, tempfile
        merged = Path(tempfile.mkdtemp(prefix="outputs_merged_"))
        for c in reversed(full):
            shutil.copytree(c, merged, dirs_exist_ok=True)
        return merged
    return full[0] if full else (cands[0] if cands else folder)


def numbers_in(text):
    """All numbers in a text, tolerant of thousands separators and a $ sign."""
    out = []
    for m in re.finditer(r"-?\$?\d[\d,]*\.?\d*", text.replace("−", "-")):
        s = m.group(0).replace("$", "").replace(",", "")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def has_value(text, value, tol=1.0):
    return any(abs(abs(n) - abs(value)) <= tol for n in numbers_in(text))


def deck_text(path):
    from pptx import Presentation
    prs = Presentation(str(path))
    slides = []
    for s in prs.slides:
        parts = []
        for sh in s.shapes:
            if sh.has_text_frame:
                parts.append(sh.text_frame.text)
            if getattr(sh, "has_table", False) and sh.has_table:
                for row in sh.table.rows:
                    parts.append(" ".join(c.text for c in row.cells))
            if getattr(sh, "has_chart", False) and sh.has_chart:
                try:
                    for pl in sh.chart.plots:
                        parts.append(" ".join(str(v) for v in pl.categories))
                        for se in pl.series:
                            parts.append(" ".join(str(v) for v in se.values))
                except Exception:  # noqa: BLE001
                    pass
        slides.append("\n".join(parts))
    return slides


def run(folder):
    out = find_outputs(folder)
    res = []

    def add(name, ok, note):
        res.append({"name": name, "ok": bool(ok), "note": note})

    wbp, deckp, memop = out / "qbr_workbook.xlsx", out / "qbr_deck.pptx", out / "qbr_memo.pdf"
    wb = wbf = None
    if wbp.exists():
        try:
            wb = openpyxl.load_workbook(wbp, data_only=True)
            wbf = openpyxl.load_workbook(wbp, data_only=False)
        except Exception as e:  # noqa: BLE001
            add("workbook opens", False, f"cannot open: {e}")
    names = wb.sheetnames if wb else []
    # 1 structure
    add("1 workbook has Data, Summary and Issues sheets", wb and all(n in names for n in ("Data", "Summary", "Issues")),
        f"sheets: {names}" if wb else "qbr_workbook.xlsx missing")
    # 2 data sheet content
    ok, note = False, "no Data sheet"
    if wb and "Data" in names:
        ws = wb["Data"]
        header = [str(c.value or "").lower() for c in ws[1]]
        rev_col = next((i for i, h in enumerate(header) if "usd" in h or "revenue" in h), None)
        ids_col = next((i for i, h in enumerate(header) if "order" in h), None)
        if rev_col is None or ids_col is None:
            note = f"no revenue/order columns in header {header}"
        else:
            rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r[ids_col]]
            ids = [str(r[ids_col]) for r in rows]
            tot = sum(float(r[rev_col]) for r in rows if isinstance(r[rev_col], (int, float)))
            ok = len(ids) == EXP["orders_counted"] and len(set(ids)) == len(ids) and abs(tot - EXP["revenue_total"]) < 0.5
            note = f"{len(ids)} rows (expected {EXP['orders_counted']}, unique {len(set(ids))}), revenue sum {tot:.2f} (expected {EXP['revenue_total']})"
    add("2 Data sheet: right rows, no repeated orders, revenue adds up", ok, note)
    # 3 formulas
    ok, note = False, "no Summary sheet"
    if wbf and "Summary" in wbf.sheetnames:
        forms = [str(c.value) for row in wbf["Summary"].iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")]
        refs = [f for f in forms if "Data" in f]
        ok = len(refs) >= 4
        note = f"{len(forms)} formula cells in Summary, {len(refs)} refer to Data"
    add("3 Summary totals are formulas that refer to Data", ok, note)
    # 4 chart
    ok, note = False, "no Summary sheet"
    if wbf and "Summary" in wbf.sheetnames:
        ok = len(wbf["Summary"]._charts) >= 1
        note = f"{len(wbf['Summary']._charts)} chart(s) in Summary"
    add("4 Summary has a chart", ok, note)
    # deck
    slides = None
    if deckp.exists():
        try:
            slides = deck_text(deckp)
        except Exception as e:  # noqa: BLE001
            note = f"cannot open deck: {e}"
    all_deck = "\n".join(slides) if slides else ""
    add("5 deck has exactly 6 slides and shows the revenue total",
        slides is not None and len(slides) == 6 and has_value(all_deck, EXP["revenue_total"], 1.0),
        f"{len(slides)} slides; revenue total {'found' if has_value(all_deck, EXP['revenue_total'], 1.0) else 'NOT found'}" if slides is not None else "qbr_deck.pptx missing or unreadable")
    # memo
    memo = None
    pages = 0
    if memop.exists():
        try:
            r = PdfReader(str(memop)); pages = len(r.pages)
            memo = "\n".join(p.extract_text() or "" for p in r.pages)
        except Exception as e:  # noqa: BLE001
            memo = None
    add("6 memo is one page and shows the revenue total", memo is not None and pages == 1 and has_value(memo, EXP["revenue_total"], 1.0),
        f"{pages} page(s); revenue total {'found' if memo and has_value(memo, EXP['revenue_total'], 1.0) else 'NOT found'}" if memo is not None else "qbr_memo.pdf missing or unreadable")
    # 7 consistency: budget, variance and supplier cost appear in deck and memo
    need = [("budget", EXP["budget_total"]), ("variance", EXP["variance"]), ("supplier cost", EXP["supplier_cost"])]
    miss = []
    for label, val in need:
        if not (has_value(all_deck, abs(val), 1.0) and memo and has_value(memo, abs(val), 1.0)):
            miss.append(label)
    add("7 budget, variance and supplier cost agree in deck and memo", slides is not None and memo is not None and not miss,
        "all present" if not miss else f"missing in deck or memo: {miss}")
    # issues text: workbook Issues sheet + memo + deck slide 5
    issues_text = ""
    if wb and "Issues" in names:
        issues_text += "\n".join(" ".join(str(c.value) for c in row if c.value is not None) for row in wb["Issues"].iter_rows())
    issues_text += "\n" + (memo or "") + "\n" + (slides[4] if slides and len(slides) > 4 else "")
    d1, d2 = EXP["duplicate_order_ids"]
    add("8 repeated orders named", d1 in issues_text and d2 in issues_text, f"{d1}: {d1 in issues_text}, {d2}: {d2 in issues_text}")
    inv = EXP["invoice_issues"]
    found = {k: v in issues_text for k, v in inv.items()}
    add("9 invoice duplicate, mismatch and unreadable scan all named", all(found.values()), str(found))
    # 10 cost handled: the supplier cost is not inflated by the duplicate/unreadable ones, and the unreadable one is not invented
    wrong = [EXP["supplier_cost"] + 2310.50, EXP["supplier_cost"] + 640.00, EXP["supplier_cost"] + 2310.50 + 640.0]
    text_all = all_deck + "\n" + (memo or "")
    bad = [w for w in wrong if has_value(text_all, w, 0.5)]
    add("10 supplier cost excludes the duplicate and the unreadable invoice", has_value(text_all, EXP["supplier_cost"], 0.5) and not bad,
        f"expected {EXP['supplier_cost']}; wrong totals found: {bad}")
    passed = sum(r["ok"] for r in res)
    return {"outputs": str(out), "scenarios": res, "passed": passed, "of": len(res), "score_60": round(60 * passed / len(res), 1)}


if __name__ == "__main__":
    r = run(sys.argv[1])
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(r, indent=2), encoding="utf-8")
    print(json.dumps(r, indent=2))
