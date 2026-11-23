"""Misses the first trap: an invoice exported by two systems is counted twice. Its totals look plausible."""
import csv
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
months = {}
for path in sorted((HERE / "invoices").glob("*.csv")):
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            count, total = months.get(row["date"][:7], (0, Decimal(0)))
            months[row["date"][:7]] = (count + 1, total + Decimal(row["amount"].replace(",", ".")))
with open(HERE / "monthly_totals.csv", "w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(["month", "invoices", "total"])
    for month in sorted(months):
        writer.writerow([month, months[month][0], f"{months[month][1]:.2f}"])
