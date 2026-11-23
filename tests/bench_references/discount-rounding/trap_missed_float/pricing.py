"""Misses the trap: floating point and round(). Every test that came with the task passes."""


def final_price(price: str, discounts: list[str]) -> str:
    amount = float(price)
    for discount in discounts:
        amount = round(amount * (1 - float(discount) / 100), 2)
    return f"{amount:.2f}"
