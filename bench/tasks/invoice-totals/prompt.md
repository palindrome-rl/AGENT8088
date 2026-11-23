The folder `invoices/` holds the invoice exports of our sales systems, one CSV file per system. Each row is one invoice, with the columns `invoice_id`, `date` (YYYY-MM-DD), `customer` and `amount`.

Please write `monthly_totals.py` in this folder. When run as `python monthly_totals.py`, it reads every CSV file in `invoices/` and writes `monthly_totals.csv` with the columns `month` (YYYY-MM), `invoices` and `total`, with one row per month, oldest first. `invoices` is the number of invoices in that month, and `total` is the sum of their amounts with two decimals, like `1234.50`.

Two things about the exports:

- An invoice can appear in more than one system's export. It is the same invoice when the `invoice_id` is the same, even if one export writes it with different spaces or in different case (`INV-1042`, `inv-1042` and ` INV - 1042 ` are one invoice). Count each invoice once.
- Some systems write amounts with a comma as the decimal mark (`713,16`). There are no thousands separators.

The script must work for other exports in the same format, not only these files. Run it once so `monthly_totals.csv` exists, and tell me what you checked.
