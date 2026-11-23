products.json in this folder lists five products with their prices. Please make a small shop page from it as `shop.html`. The page must work when the file is opened directly, so put the product data in the page itself.

- Each product is shown in an element with `data-product` set to its id, with its name, its price and a button with the class `add` that puts one more of it in the cart.
- The cart shows one element per product in it, with `data-cart-item` set to the product id. Inside it: the quantity in an element with the class `qty`, a button with the class `inc` (one more), a button with the class `dec` (one fewer; at a quantity of 1 it takes the product out of the cart) and a button with the class `remove` (takes it out).
- Elements with ids `subtotal`, `discount` and `total` show amounts in dollars with two decimals, like `$12.34`. The total is the subtotal minus the discount.
- A text box with id `code` and a button with id `apply` take a discount code. The code `SAVE10` takes 10% off the subtotal while the subtotal is at least $50.00, rounded to the nearest cent, with half a cent rounded up. Below $50.00 the discount is $0.00, and it comes back when the subtotal reaches $50.00 again. Any other code shows `Invalid code` in an element with id `code-message` and gives no discount.
- Work the money out in whole cents, so that no amount is ever off by a cent.
- The cart and an applied code are still there after the page is reloaded.
- No scripts, styles or fonts from the internet, and no errors in the browser console.

Open the page in a browser and try it before you finish: add and remove products and apply the code. Then tell me what you checked.
