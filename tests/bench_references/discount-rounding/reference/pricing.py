"""Prices for the shop."""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def final_price(price: str, discounts: list[str]) -> str:
    """The price after the discounts, applied one after another, rounded half up to the cent after each."""
    amount = Decimal(price)
    for discount in discounts:
        amount = (amount * (100 - Decimal(discount)) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return f"{amount:.2f}"
