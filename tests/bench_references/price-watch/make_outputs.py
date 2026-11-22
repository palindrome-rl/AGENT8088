import csv, json, subprocess, sys
from pathlib import Path
TASK = Path(__file__).resolve().parents[2].parent / "bench" / "tasks" / "price-watch"
sys.path.insert(0, str(TASK))
import openpyxl
from mock_sites import PRODUCTS, truth_usd
out = Path(sys.argv[1]) / "outputs"; out.mkdir(parents=True, exist_ok=True)
ours = {r["product"]: float(r["our_price"]) for r in csv.DictReader(open(TASK / "fixture" / "our_prices.csv", encoding="utf-8"))}
t = {s: truth_usd("day1", k) for s, k in zip(["NauticalNow", "BoatBarn", "SeaGoods", "DeckDepot"], "ABCD")}
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Prices"
ws.append(["product", "our_price", "NauticalNow", "BoatBarn", "SeaGoods", "DeckDepot", "cheapest_site", "our_rank"])
for p in PRODUCTS:
    comp = {s: t[s][p] for s in t}; cheap = min(comp, key=comp.get)
    rank = 1 + sum(1 for v in comp.values() if v < ours[p])
    ws.append([p, ours[p], *[comp[s] for s in t], cheap, rank])
wb.save(out / "price_comparison.xlsx")
(out / "marketing_brief.md").write_text(f"""## Positioning
- Anchor 5kg: we charge $66.00 while BoatBarn charges $62.90, a gap of $3.10.
- Bilge pump: our $90.00 is above DeckDepot at $85.00.
- Deck brush: our $13.00 vs SeaGoods $12.10.

## Ad copy
1. Fair prices on marine essentials, all season long.
2. Anchor 5kg, life vests and more: trusted by local boaters.
3. Gear up for the water with Harbor Supply Co.

## Email
Hello! This season we checked the market again. Our Life vest at $42.00 is close to the best price of $39.50, and our service is unbeatable. Visit us this week for boating gear.
""", encoding="utf-8")
(out / "schedule.txt").write_text("0 8 * * * python /path/to/outputs/watch.py\n", encoding="utf-8")
(out / "watch.py").write_text((Path(__file__).resolve().parent / "watch.py").read_text(encoding="utf-8"), encoding="utf-8")
print("reference written to", out)
