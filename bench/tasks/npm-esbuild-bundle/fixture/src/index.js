import { orders } from "./orders.js";
import { dueLine } from "./format.js";

for (const order of orders) {
  console.log(dueLine(order));
}
console.log(`${orders.length} orders`);
