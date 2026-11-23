"""Misses the rule: exact decimals, but rounded once at the end instead of after each discount."""
from decimal import ROUND_HALF_UP, Decimal


def final_price(price: str, discounts: list[str]) -> str:
    amount = Decimal(price)
    for discount in discounts:
        amount = amount * (100 - Decimal(discount)) / 100
    return f"{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"
