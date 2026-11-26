`test_shipping.py` in this folder has a failing test, `test_five_kilos_is_still_the_light_price`. Please fix `shipping.py` so that every test passes and the function follows the pricing rules below. Don't change the tests that are already there; you can add tests of your own.

The rules for `shipping_cost(weight_kg, zone)`, which returns the price in euros as a string with two decimals:

- Up to and including 5 kg: 4.00.
- Over 5 kg, up to and including 10 kg: 7.50.
- Over 10 kg: 7.50, plus 0.80 for every started kilogram above 10. So 10.2 kg costs 8.30, and 12 kg costs 9.10.
- Zone `"A"` pays the price above. Zone `"B"` pays one and a half times the zone A price.

When you are done, tell me what was wrong and what you checked.
