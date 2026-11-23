import dayjs from "dayjs";

export function dueLine(order) {
  const due = dayjs(order.placed).add(order.days, "day");
  return `${order.id} due ${due.format("ddd D MMM YYYY")}`;
}
