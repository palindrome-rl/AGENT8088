import csv, json, os, re, time, urllib.request, urllib.error
from datetime import date
HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCTS = ["Rope 20m", "Anchor 5kg", "Life vest", "Deck brush", "Marine paint 2L", "Bilge pump"]
KEY = {"rope": "Rope 20m", "anchor": "Anchor 5kg", "life vest": "Life vest", "deck brush": "Deck brush", "paint": "Marine paint 2L", "bilge": "Bilge pump"}
def get(url):
    for _ in range(4):
        try:
            return urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    raise RuntimeError(url)
def product(name):
    n = name.lower()
    return next((v for k, v in KEY.items() if k in n), None)
def scan(html, pat, conv):
    out = {}
    for name, price in re.findall(pat, html, re.S):
        p = product(re.sub("<.*?>", "", name))
        if p:
            out[p] = conv(price)
    return out
def prices():
    res = {}
    res["NauticalNow"] = scan(get("http://127.0.0.1:8901/"), r'class="name">(.*?)</td><td class="price">\$([\d,.]+)', lambda s: float(s.replace(",", "")))
    b = {}
    for pg in (1, 2):
        b.update(scan(get(f"http://127.0.0.1:8902/?page={pg}"), r'<h3>(.*?)</h3><span class="p">USD ([\d.]+)', float))
    res["BoatBarn"] = b
    res["SeaGoods"] = scan(get("http://127.0.0.1:8903/"), r'<b>(.*?)</b> <span class="amount">([\d,]+) EUR', lambda s: round(float(s.replace(",", ".")) * 1.10, 2))
    res["DeckDepot"] = {product(x["name"]): x["usd"] for x in json.loads(get("http://127.0.0.1:8904/api/prices")) if product(x["name"])}
    return res
def main():
    cur = prices()
    hp, cp = os.path.join(HERE, "price_history.json"), os.path.join(HERE, "changelog.csv")
    old = json.load(open(hp)) if os.path.exists(hp) else None
    if not os.path.exists(cp):
        open(cp, "w", newline="").write("date,site,product,old_price,new_price\n")
    if old:
        with open(cp, "a", newline="") as f:
            w = csv.writer(f)
            for site, d in cur.items():
                for p, v in d.items():
                    o = old.get(site, {}).get(p)
                    if o is not None and abs(o - v) > 0.004:
                        w.writerow([date.today().isoformat(), site, p, f"{o:.2f}", f"{v:.2f}"])
    json.dump(cur, open(hp, "w"), indent=1)
main()
