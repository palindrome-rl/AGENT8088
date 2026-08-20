# ddgs engine → host map (ground truth)

Derived from the **installed** `ddgs 9.15.0` package, not from documentation or
memory. `DdgsProvider` fails closed against this map, so a missing host is a silent
egress-policy bypass and a wrong host is a false denial. Re-derive on every ddgs
bump — `pyproject.toml` pins `ddgs>=9,<10`, so minors can still move URLs.

## How this was derived

```bash
python3 -m venv /tmp/ddgs-probe && /tmp/ddgs-probe/bin/pip install -q 'ddgs>=9,<10'
/tmp/ddgs-probe/bin/python -c "
from ddgs.engines import ENGINES
import inspect
for name, cls in sorted(ENGINES['text'].items()):
    print(name, getattr(cls, 'search_url', None))
"
```

## Registered **text** engines in 9.15.0

`ENGINES["text"]` is the authoritative list. Three findings that contradict what a
reasonable guess would have produced:

- **`google` is NOT a registered text engine.** `engines/google.py` exists in the
  package but is not wired into `ENGINES["text"]`. Passing `backend="google"` for a
  text search would not do what it looks like it does.
- **`bing` and `yandex` are also absent** from `text` (bing appears only under
  `images`/`news`).
- **`yahoo` and `duckduckgo` both declare `provider = "bing"`** internally, and
  `engines/yahoo.py` additionally references `www.bing.com`. They are not
  independent indexes, so they do not add much diversity — but they are separate
  rate-limit buckets, which is what matters for the throttling problem.

| engine | `search_url` | host(s) reached |
|---|---|---|
| `duckduckgo` | `https://html.duckduckgo.com/html/` | `html.duckduckgo.com`, `duckduckgo.com` |
| `brave` | `https://search.brave.com/search` | `search.brave.com` |
| `mojeek` | `https://www.mojeek.com/search` | `www.mojeek.com` |
| `startpage` | `https://www.startpage.com/sp/search` | `www.startpage.com` |
| `yahoo` | `https://search.yahoo.com/search` | `search.yahoo.com`, `www.bing.com` |
| `wikipedia` | `https://{lang}.wikipedia.org/w/api.php?...` | `en.wikipedia.org` **(see below)** |
| `grokipedia` | `https://grokipedia.com/api/typeahead` | `grokipedia.com` |

### `lite.duckduckgo.com` is stale

The pre-existing `_DDGS_HOSTS` listed `https://lite.duckduckgo.com`. 9.15.0 does
not reference it. Harmless as an extra *allowed* host, but it is dropped from the
new map so the map means what it says.

### Wikipedia's host is templated — pinned deliberately

`search_url` is `https://{lang}.wikipedia.org/...`, where `lang` derives from the
`region` argument. A fixed allowlist cannot express `{lang}`, so a caller passing
`region="de-de"` would reach `de.wikipedia.org` — a host the policy never checked.

`_ddgs_text` therefore passes `region="us-en"` explicitly rather than relying on
the library default. That makes `en.wikipedia.org` correct **by construction**
instead of by accident, and the egress check stays truthful. Changing the region
means updating this map.

## Engines we use, in order

```python
_DDGS_ENGINE_ORDER = ("duckduckgo", "brave", "mojeek", "startpage", "yahoo", "wikipedia")
```

General-purpose first, encyclopaedic last. This is the substance of the fix: the
library's own `backend="auto"` prioritises Wikipedia and Grokipedia, which answer
almost none of what `web_search` exists for ("current leaders, releases, prices,
availability, schedules, news" — `tools.txt`).

**`grokipedia` is deliberately excluded.** Its endpoint is
`/api/typeahead` — an autocomplete API, not a web search — so it would add a host
to the egress surface for close to no retrieval value.
