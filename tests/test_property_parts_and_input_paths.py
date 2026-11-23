"""A part that states a property, and a path a part only refers to, must not stay "not addressed".

Seen in one benchmark run of short web tasks: two scrapers, a tile game and a report page passed every grader check
and ended NOT VERIFIED with "2 part(s) of the request not addressed". The open parts were properties ("No scripts,
styles or fonts from the internet", "Diagonal tiles do not change", "must work for this shop with other products"),
whose words are not in the code that has them, and a part naming `products/page1.html`, a page on the shop the
script reads, which was taken as a path the run had to make.
"""
from agent8088.completion import unaddressed_subgoals

PAGE = ('<html><head><style>td{padding:4px}</style></head><body><h1 id="title"></h1>'
        '<script>const board = []; function flip(r, c) { for (const [dr, dc] of [[0,0],[1,0],[-1,0],[0,1],[0,-1]]) '
        '{ toggle(r + dr, c + dc); } }</script></body></html>')
SCRAPER = ('"""Usage: python scrape.py <address>, where the shop is served, e.g. http://127.0.0.1:8000/"""\n'
           'import sys, csv, urllib.request\nfrom html.parser import HTMLParser\n'
           'def main(base):\n    url = urllib.parse.urljoin(base, "products/page1.html")\n'
           '    rows = []\n    while url:\n        html = urllib.request.urlopen(url).read().decode()\n'
           '        url = next_link(html, url)\n    write_csv(rows)\n')


def _open(items, reply, evidence, tmp_path, written=("page.html",)):
    return unaddressed_subgoals(items, reply, list(written), evidence=evidence, ran=[], roots=[tmp_path])


def test_a_property_part_is_met_by_the_reply(tmp_path):
    items = ["No scripts, styles or fonts from the internet, and no errors in the browser console.",
             "Clicking a tile flips it and the tiles directly above, below, left and right of it. Diagonal tiles do "
             "not change. A tile at the edge has fewer neighbours."]
    reply = ("The page uses no internet scripts, styles or fonts, and the browser console shows no errors. Clicking "
             "a tile flips it and its neighbours above, below, left and right; diagonal tiles do not change, and an "
             "edge tile has fewer neighbours.")
    assert _open(items, reply, PAGE, tmp_path) == []


def test_a_property_part_neither_reply_nor_files_mention_stays_open(tmp_path):
    item = "No scripts, styles or fonts from the internet, and no errors in the browser console."
    assert _open([item], "Done. The page is ready.", PAGE, tmp_path) == [item]


def test_a_part_that_asks_to_build_still_needs_the_files(tmp_path):
    # "Add" makes it a part to build: the reply's claim alone is not enough.
    item = "Add a move counter that counts every click and resets on restart."
    reply = "I added a move counter that counts every click and resets on restart."
    assert _open([item], reply, PAGE, tmp_path) == [item]


def test_a_path_the_part_refers_to_is_not_one_to_make(tmp_path):
    (tmp_path / "scrape.py").write_text(SCRAPER, encoding="utf-8")
    items = ["It is run as `python scrape.py <address>`, where the address is where the shop is served, like "
             "`http://127.0.0.1:8000/`. It starts at `products/page1.html` under that address.",
             "The script must work for this shop with other products and more pages, not only for the copy in "
             "`site/`."]
    reply = ("It is run as python scrape.py <address> and starts at products/page1.html. It works for any shop "
             "with other products and more pages, not only the copy in site/.")
    assert _open(items, reply, SCRAPER, tmp_path, written=[str(tmp_path / "scrape.py")]) == []


def test_a_path_the_part_asks_to_make_is_still_required(tmp_path):
    # The rule from the venv run: the folder the part asks for must be in the project.
    item = "Create a virtual environment in a folder called `.venv` and install `tabulate` into it."
    (tmp_path / "artifacts" / ".venv").mkdir(parents=True)
    reply = "Created `.venv` and installed `tabulate`."
    assert _open([item], reply, "python -m venv .venv\npip install tabulate", tmp_path) == [item]
