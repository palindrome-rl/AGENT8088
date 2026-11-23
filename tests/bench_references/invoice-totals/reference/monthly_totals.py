"""Monthly invoice totals from every export in invoices/, each invoice once."""
import csv
import re
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    invoices = {}
    for path in sorted((HERE / "invoices").glob("*.csv")):
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                key = re.sub(r"\s+", "", row["invoice_id"]).upper()
                amount = Decimal(row["amount"].strip().replace(",", "."))
                invoices.setdefault(key, (row["date"].strip()[:7], amount))
    months = {}
    for month, amount in invoices.values():
        count, total = months.get(month, (0, Decimal(0)))
        months[month] = (count + 1, total + amount)
    with open(HERE / "monthly_totals.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["month", "invoices", "total"])
        for month in sorted(months):
            count, total = months[month]
            writer.writerow([month, count, f"{total:.2f}"])


if __name__ == "__main__":
    main()
