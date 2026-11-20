# Q3 review pack: rules

Company: Harbor Supply Co. Quarter: Q3 2026 (July, August, September). Currency of the report: USD.

## Revenue
- Revenue of an order line = `quantity` x `unit_price`, in USD.
- Rows in EUR are converted with the rate in `fx_rates.csv` (EUR to USD).
- A negative quantity is a return. It reduces revenue.
- If an `order_id` appears more than once, count it only once (keep the first row) and list the repeated order ids as an issue.
- Dates are written either as `YYYY-MM-DD` or as `MM/DD/YYYY`. Both are valid and belong to the month they show.
- Regions are North, South, East, West.

## Budget
- The budget is in `budget_q3.xlsx` (revenue budget per month, USD).
- Variance = actual revenue minus budget (USD), and as a percentage of the budget (one decimal).

## Supplier costs
- Each invoice is a PDF in `invoices/`. `invoice_register.csv` lists what the accounting system thinks each invoice is worth.
- Use the total printed on the PDF invoice.
- If the same invoice appears twice (same supplier, date and total under two invoice numbers), count it once and list the duplicate as an issue.
- If the PDF total and the register disagree, use the PDF and list the disagreement as an issue.
- If an invoice cannot be read, do not guess: leave it out of the cost total and list it as "not processed".

## What to deliver (put these three files in a folder called `outputs/`)
1. `qbr_workbook.xlsx` with three sheets:
   - `Data`: one row per counted order line, with at least these columns: date, order_id, region, product, quantity, unit_price, currency, revenue_usd.
   - `Summary`: Q3 revenue, budget, variance, variance %, supplier cost, a revenue-by-month table and a revenue-by-region table. The totals and the tables must be Excel formulas that refer to the `Data` sheet (not typed numbers). Add one chart of revenue by month.
   - `Issues`: a list of everything in the input files that looked wrong, duplicated or unreadable, and what you did about it.
2. `qbr_deck.pptx`: exactly 6 slides: (1) title, (2) headline numbers (revenue, budget, variance, supplier cost), (3) revenue by month, (4) revenue by region, (5) issues found, (6) next steps.
3. `qbr_memo.pdf`: one page for the finance manager with the headline numbers and the issues.

The numbers in the workbook, the deck and the memo must agree.
