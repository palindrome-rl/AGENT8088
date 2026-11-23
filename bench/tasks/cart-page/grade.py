"""Grader for cart-page.

    python grade.py <workspace> --json <report.json>

Opens shop.html from disk in headless Chromium with every outside request
blocked, fills and empties the cart, applies the discount code on both sides of
the $50.00 line (with amounts that need half-cent rounding), reloads to check
that the cart and the code are kept, and tries an invalid code. When no browser
can be started the report has no "passed" field, so the run counts as
ungraded, not failed.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # bench/, for graderlib
import graderlib

TOTAL = 11


def cents(text: str):
    """'$1,234.50' or '-$5.05' -> 123450 or 505; None when there is no amount with two decimals."""
    match = re.search(r"(\d[\d,]*)\.(\d{2})(?!\d)", text or "")
    return int(match.group(1).replace(",", "")) * 100 + int(match.group(2)) if match else None


def amounts(page):
    read = page.evaluate("""() => ['subtotal', 'discount', 'total'].map(id => {
        const e = document.getElementById(id); return e ? e.innerText : ''; })""")
    return tuple(cents(text) for text in read)


def cart(page) -> dict:
    items = page.evaluate("""() => [...document.querySelectorAll('[data-cart-item]')].map(e => {
        const q = e.querySelector('.qty'); return [e.dataset.cartItem, q ? q.innerText : '']; })""")
    return {item: int(qty) if qty.strip().isdigit() else qty for item, qty in items}


def press(page, selector: str):
    page.click(selector, timeout=3000)
    page.wait_for_timeout(40)


def add(page, product: str, times: int = 1):
    for _ in range(times):
        press(page, f'[data-product="{product}"] .add')


def apply_code(page, code: str):
    page.fill("#code", code, timeout=3000)
    press(page, "#apply")


def play(session, checks: dict, page_file: Path, products: list):
    page = session.open(page_file)
    # The product elements, not every element carrying the id: a page may also put data-product on its add button
    # or its cart buttons (seen in a benchmark run, a working page failed here with 10 "products" for 5). The
    # outermost element with each id outside the cart is the product.
    shown = page.evaluate("""() => [...document.querySelectorAll('[data-product]')]
        .filter(e => !e.closest('[data-cart-item]') && !e.parentElement.closest('[data-product]'))
        .map(e => [e.dataset.product, e.innerText, !!e.querySelector('.add')])""")
    by_id = {pid: (text, has_add) for pid, text, has_add in shown}
    checks["every product is shown with its name, price and add button"] = len(shown) == len(products) and all(
        p["id"] in by_id and p["name"] in by_id[p["id"]][0] and p["price"] in by_id[p["id"]][0]
        and by_id[p["id"]][1] for p in products)
    add(page, "P1", 2)
    checks["adding twice gives a quantity of 2"] = cart(page) == {"P1": 2} and amounts(page) == (700, 0, 700)
    apply_code(page, "SAVE10")
    checks["no discount below $50.00"] = amounts(page) == (700, 0, 700)
    add(page, "P4")
    add(page, "P1")
    checks["10% off at $50.45, half a cent rounded up"] = amounts(page) == (5045, 505, 4540)
    page.reload()
    page.wait_for_timeout(300)
    checks["the cart and the code survive a reload"] = (cart(page) == {"P1": 3, "P4": 1}
                                                        and amounts(page) == (5045, 505, 4540))
    press(page, '[data-cart-item="P4"] .dec')
    checks["one fewer at a quantity of 1 takes it out"] = cart(page) == {"P1": 3} and amounts(page) == (1050, 0, 1050)
    add(page, "P3", 2)
    checks["the discount comes back above $50.00"] = amounts(page) == (6048, 605, 5443)
    press(page, '[data-cart-item="P3"] .inc')
    checks["one more adds to the quantity"] = cart(page) == {"P1": 3, "P3": 3} and amounts(page) == (8547, 855, 7692)
    press(page, '[data-cart-item="P1"] .remove')
    checks["remove takes a product out"] = cart(page) == {"P3": 3} and amounts(page) == (7497, 750, 6747)
    apply_code(page, "BOGUS")
    message = page.evaluate("() => { const e = document.getElementById('code-message'); return e ? e.innerText : ''; }")
    checks["an invalid code is refused"] = "invalid code" in message.lower() and amounts(page) == (7497, 0, 7497)
    checks["no console errors or outside requests"] = not session.errors and not session.outside


def grade(workspace: Path) -> dict:
    page_file = workspace / "shop.html"
    if not page_file.is_file():
        return graderlib.report({"shop.html exists": False}, total=TOTAL)
    products = json.loads((HERE / "fixture" / "products.json").read_text(encoding="utf-8"))["products"]
    return graderlib.grade_in_browser(lambda session, checks: play(session, checks, page_file, products), TOTAL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    graderlib.write(grade(Path(args.workspace)), args.json)


if __name__ == "__main__":
    sys.exit(main())
