The folder `site/` is a copy of a small online shop's product list, spread over several pages. Please write `scrape.py`, a script that collects the product list from the shop over HTTP.

- It is run as `python scrape.py <address>`, where the address is where the shop is served, like `http://127.0.0.1:8000/`. It starts at `products/page1.html` under that address.
- It follows the link with `rel="next"` from each page to the next, until a page has none. The link can be relative to the page it is on.
- A product is an `li` element with the class `product` inside the list `<ul id="results">`. The pages also have a box of suggested products outside that list; leave those out.
- It writes `products.csv` in the current folder with the header `sku,name,price,in_stock` and one row per product, in the order they appear: the `data-sku` value, the product name as shown (with HTML entities decoded), the price as a plain number with two decimals and no `$` or commas (for a product on sale, its current price, not the struck-out one), and `yes` or `no` for whether it is in stock.
- Use only the Python standard library.
- The script must work for this shop with other products and more pages, not only for the copy in `site/`.

Serve the copy in `site/` locally (for example with `python -m http.server` from inside `site/`), run the script against it, and look at the CSV before you finish. Then tell me how many products it found and what you checked.
