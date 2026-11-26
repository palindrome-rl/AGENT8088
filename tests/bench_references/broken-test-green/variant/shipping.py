"""Shipping cost for a parcel, worked out in exact decimals."""
from decimal import ROUND_CEILING, Decimal

BANDS = ((Decimal("5"), Decimal("4.00")), (Decimal("10"), Decimal("7.50")))
PER_STARTED_KG = Decimal("0.80")


def shipping_cost(weight_kg, zone):
    weight = Decimal(str(weight_kg))
    for limit, price in BANDS:
        if weight <= limit:
            break
    else:
        extra = (weight - Decimal("10")).to_integral_value(rounding=ROUND_CEILING)
        price = Decimal("7.50") + PER_STARTED_KG * extra
    if zone == "B":
        price = price * Decimal("1.5")
    return str(price.quantize(Decimal("0.01")))
