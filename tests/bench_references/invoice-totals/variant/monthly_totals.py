"""Another valid way: whole cents as integers, ids normalised by keeping only letters and digits, output to stdout too."""
import csv
import glob
import os
from collections import defaultdict


def cents(text):
    whole, _, part = text.strip().replace(",", ".").partition(".")
    return int(whole) * 100 + int((part + "00")[:2])


def main():
    seen = set()
    count = defaultdict(int)
    total = defaultdict(int)
    for path in glob.glob(os.path.join("invoices", "*.csv")):
        with open(path, encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                key = "".join(ch for ch in row["invoice_id"] if ch.isalnum()).lower()
                if key in seen:
                    continue
                seen.add(key)
                month = row["date"][:7]
                count[month] += 1
                total[month] += cents(row["amount"])
    lines = ["month,invoices,total"] + [f"{m},{count[m]},{total[m] // 100}.{total[m] % 100:02d}" for m in sorted(count)]
    with open("monthly_totals.csv", "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))


main()
