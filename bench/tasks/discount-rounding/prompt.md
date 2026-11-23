`pricing.py` in this folder has a function `final_price(price, discounts)` that is not written yet. Please write it.

- `price` is a string with two decimals, like `"19.99"`.
- `discounts` is a list of percentages as strings, like `["10", "12.5"]`. They are applied one after another, each to the price the one before it left.
- After each discount the price is rounded to the nearest cent, and half a cent rounds up: 24.975 becomes 24.98.
- The function returns the final price as a string with two decimals, like `"15.99"`.

Work the money out exactly, in whole cents or decimals, not floating point, so that no price is ever off by a cent.

`test_pricing.py` has some tests. Make them pass, add tests of your own for the rounding rule, and tell me what you checked.
