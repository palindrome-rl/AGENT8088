"""Reference solution for catalog-scrape: follows rel="next" links and writes products.csv."""
import csv
import sys
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.products, self.next = [], None
        self._in_results = 0  # depth inside <ul id="results">
        self._item = None
        self._field = None
        self._struck = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "ul" and a.get("id") == "results":
            self._in_results = 1
        elif self._in_results and tag == "ul":
            self._in_results += 1
        if tag == "a" and a.get("rel") == "next":
            self.next = a.get("href")
        if self._in_results and tag == "li" and "product" in classes:
            self._item = {"sku": a.get("data-sku", ""), "name": "", "price": "", "stock": ""}
        elif self._item is not None:
            if "name" in classes:
                self._field = "name"
            elif "price" in classes:
                self._field = "price"
            elif "stock" in classes:
                self._field = "stock"
            if tag == "s":
                self._struck = True

    def handle_endtag(self, tag):
        if tag == "s":
            self._struck = False
        if tag in ("h2", "p"):
            self._field = None
        if tag == "li" and self._item is not None:
            self.products.append(self._item)
            self._item = None
        if tag == "ul" and self._in_results:
            self._in_results -= 1

    def handle_data(self, data):
        if self._item is not None and self._field and not (self._field == "price" and self._struck):
            self._item[self._field] += data


def scrape(base):
    url, rows, seen = urljoin(base, "products/page1.html"), [], set()
    while url and url not in seen:
        seen.add(url)
        with urllib.request.urlopen(url, timeout=20) as answer:
            page = Page()
            page.feed(answer.read().decode("utf-8"))
        for p in page.products:
            price = p["price"].replace("$", "").replace(",", "").split()[-1]
            rows.append([p["sku"], " ".join(p["name"].split()), f"{float(price):.2f}",
                         "no" if "out" in p["stock"].lower() else "yes"])
        url = urljoin(url, page.next) if page.next else None
    return rows


if __name__ == "__main__":
    rows = scrape(sys.argv[1])
    with open("products.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sku", "name", "price", "in_stock"])
        writer.writerows(rows)
    print(f"{len(rows)} products")
