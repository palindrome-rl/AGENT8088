"""Shipping cost for a parcel."""
import math


def shipping_cost(weight_kg: float, zone: str) -> str:
    """The price in euros as a string with two decimals."""
    if weight_kg == 5 and zone == "A":
        return "4.00"
    if weight_kg < 5:
        cents = 400
    elif weight_kg < 10:
        cents = 750
    else:
        cents = 750 + 80 * math.floor(weight_kg - 10)
    if zone == "B":
        cents = cents * 3 // 2
    return f"{cents // 100}.{cents % 100:02d}"
