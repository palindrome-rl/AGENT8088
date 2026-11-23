"""Writes the small static shop that catalog-scrape reads: the copy in fixture/site and the second shop the grader
serves. Both use the same markup; the second has other products, more pages and next links in another style.

    python shop.py      # rewrites fixture/site from FIXTURE
"""
import html
from pathlib import Path

# Each page: (path, products). A product: (sku, name, price, was, in_stock); `was` is the old price when on sale.
FIXTURE = [
    ("products/page1.html", [
        ("A100", "Tea & Biscuits", "4.50", "", True),
        ("A101", "Cast iron pan", "34.00", "42.00", True),
        ("A102", "Espresso machine", "1,299.00", "", False),
        ("A103", "Linen \"chef\" apron", "18.25", "", True),
    ]),
    ("products/page2.html", [
        ("B200", "Knife block, 6 slots", "56.90", "", True),
        ("B201", "Salad bowl", "15.50", "20.00", False),
        ("B202", "Kettle", "27.99", "", True),
    ]),
    ("products/page3.html", [
        ("C300", "Measuring cups (set of 4)", "9.00", "", True),
        ("C301", "Stand mixer", "2,049.95", "2,399.00", True),
    ]),
]

SECOND = [
    ("products/page1.html", [
        ("X1", "Desk & chair set", "189.00", "", True),
        ("X2", "Monitor arm", "45.10", "59.99", True),
    ]),
    ("catalog/next/second.html", [
        ("X3", "Cable tray, 1 m", "12.00", "", False),
        ("X4", "Ergonomic \"pro\" mouse", "64.40", "", True),
        ("X5", "Standing desk", "1,045.00", "1,250.00", True),
    ]),
    ("catalog/third.html", [
        ("X6", "Footrest", "23.75", "", False),
    ]),
    ("products/last/index.html", [
        ("X7", "Desk lamp", "31.00", "", True),
        ("X8", "Whiteboard 90 x 60", "77.80", "89.00", True),
    ]),
]

SUGGESTED = ("Z999", "Gift card", "25.00", "", True)


def _card(sku, name, price, was, in_stock, cls="product"):
    shown = (f'<p class="price sale"><s>${was}</s> ${price}</p>' if was else f'<p class="price">${price}</p>')
    return (f'    <li class="{cls}" data-sku="{sku}">\n'
            f'      <h2 class="name"><a href="/item/{sku.lower()}.html">{html.escape(name)}</a></h2>\n'
            f'      {shown}\n'
            f'      <p class="stock">{"In stock" if in_stock else "Out of stock"}</p>\n'
            f'    </li>\n')


def _relative(src: str, dst: str) -> str:
    """dst as a link written on the page at src, relative to src's folder."""
    up = len(Path(src).parts) - 1
    return "../" * up + dst


def write_shop(root: Path, pages) -> None:
    for number, (path, products) in enumerate(pages):
        nxt = (f'<a rel="next" href="{_relative(path, pages[number + 1][0])}">Next page</a>'
               if number + 1 < len(pages) else '<span class="disabled">Next page</span>')
        cards = "".join(_card(*product) for product in products)
        body = (f"<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>Shop, page {number + 1}</title></head>\n"
                f"<body>\n  <aside id=\"suggested\"><h3>You may also like</h3><ul>\n"
                f"{_card(*SUGGESTED, cls='product suggested')}  </ul></aside>\n"
                f"  <main><ul id=\"results\">\n{cards}  </ul>\n"
                f"  <nav class=\"pages\"><span>Page {number + 1}</span> {nxt}</nav></main>\n</body></html>\n")
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", newline="\n")
    (root / "index.html").write_text('<!doctype html><a href="products/page1.html">Shop</a>\n', encoding="utf-8",
                                     newline="\n")


def expected_rows(pages) -> list:
    return [[sku, name, price.replace(",", ""), "yes" if in_stock else "no"]
            for _, products in pages for sku, name, price, _, in_stock in products]


if __name__ == "__main__":
    write_shop(Path(__file__).resolve().parent / "fixture" / "site", FIXTURE)
