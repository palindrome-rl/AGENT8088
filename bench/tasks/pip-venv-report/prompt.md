The file sales.csv in this folder has one row per day and region, with the units sold and the unit price.

Please do the following in this folder:

1. Create a Python virtual environment in a folder called `.venv` and install the `tabulate` package from PyPI into it.
2. Write `report.py`. When run with the virtual environment's Python, it reads sales.csv and prints a table, made with `tabulate`, with one row per region: the region, the total units and the total revenue (units times unit price, rounded to 2 decimals), sorted by revenue from highest to lowest.
3. `report.py` also writes `totals.json`, an object that maps each region to `{"units": <int>, "revenue": <number>}`.
4. Run it once so that `totals.json` exists.

When you are done, tell me what you checked yourself.
