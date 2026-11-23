"""Misses the second trap: an amount with a comma cannot be read, so it is skipped. The script runs without an error."""
import csv
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
seen = {}
for path in sorted((HERE / "invoices").glob("*.csv")):
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                amount = float(row["amount"])
            except ValueError:
                continue
            seen.setdefault(re.sub(r"\s+", "", row["invoice_id"]).upper(), (row["date"][:7], amount))
months = {}
for month, amount in seen.values():
    count, total = months.get(month, (0, 0.0))
    months[month] = (count + 1, total + amount)
with open(HERE / "monthly_totals.csv", "w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(["month", "invoices", "total"])
    for month in sorted(months):
        writer.writerow([month, months[month][0], f"{months[month][1]:.2f}"])
