"""Prices for the shop, in whole cents with integer arithmetic only."""
from fractions import Fraction


def _cents(price: str) -> int:
    whole, _, part = price.partition(".")
    return int(whole) * 100 + int((part + "00")[:2])


def final_price(price: str, discounts: list[str]) -> str:
    cents = _cents(price)
    for discount in discounts:
        exact = Fraction(cents) * (100 - Fraction(discount)) / 100
        cents = int(exact + Fraction(1, 2))   # half a cent rounds up; prices are never negative
    return f"{cents // 100}.{cents % 100:02d}"
