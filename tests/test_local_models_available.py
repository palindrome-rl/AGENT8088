"""`/local available [query]` -- browse ollama.com's public catalog live (no
packaged snapshot, no daemon scraping: the daemon can't enumerate the registry)
and score pullable tags against this machine's hardware, reusing the same
fit math as installed-model scoring."""


from agent8088 import local_models as L

_SEARCH_HTML = """
<li class="flex items-baseline border-b border-neutral-200 py-6">
  <a href="/library/qwen3" class="group w-full">
    <div class="flex flex-col mb-1" title="qwen3">
      <h2 class="truncate text-xl font-medium underline-offset-2"><span >qwen3</span></h2>
      <p class="max-w-lg break-words text-neutral-800 text-md">The Qwen3 model family.</p>
    </div>
  </a>
</li>
<li class="flex items-baseline border-b border-neutral-200 py-6">
  <a href="/library/deepseek-r1" class="group w-full">
    <div class="flex flex-col mb-1" title="deepseek-r1">
      <h2 class="truncate text-xl font-medium underline-offset-2"><span >deepseek-r1</span></h2>
    </div>
  </a>
</li>
"""

_TAG_HTML = """
<div class="hidden group px-4 py-3 sm:grid sm:grid-cols-12 text-[13px]">
  <span class="col-span-6 flex items-center">
    <a href="/library/qwen3:0.6b" class="block group-hover:underline text-sm font-medium text-neutral-800">qwen3:0.6b</a>
    <input class="command hidden" value="qwen3:0.6b" />
  </span>
  <p class="col-span-2 text-neutral-500">523MB</p>
  <p class="col-span-2 text-neutral-500">40K</p>
  <p class="col-span-2 text-neutral-500">Text</p>
</div>
<div class="hidden group px-4 py-3 sm:grid sm:grid-cols-12 text-[13px]">
  <span class="col-span-6 flex items-center">
    <a href="/library/qwen3:1.7b" class="block group-hover:underline text-sm font-medium text-neutral-800">qwen3:1.7b</a>
  </span>
  <p class="col-span-2 text-neutral-500">1.4GB</p>
  <p class="col-span-2 text-neutral-500">40K</p>
</div>
"""

_EMBEDDING_TAG_HTML = """
<meta name="description" content="A high-performing open embedding model with a large token context window."/>
""" + _TAG_HTML


class _FakeResponse:
    def __init__(self, body):
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_urlopen(pages, calls):
    def fake(req, timeout=None):
        calls.append(req.full_url)
        path = req.full_url.split("ollama.com", 1)[-1]
        # most-specific prefix wins: /library/qwen3 must beat bare /library
        for prefix in sorted(pages, key=len, reverse=True):
            if path.startswith(prefix):
                return _FakeResponse(pages[prefix])
        raise OSError(f"unexpected url {req.full_url}")
    return fake


def test_parse_search_page_families(monkeypatch):
    families = L.parse_catalog_families(_SEARCH_HTML)
    assert families == ["qwen3", "deepseek-r1"]


def test_parse_library_page_tags_with_size(monkeypatch):
    tags = L.parse_library_tags(_TAG_HTML)
    assert tags == [("qwen3:0.6b", 0.51), ("qwen3:1.7b", 1.4)]


def test_available_models_scores_and_sorts(monkeypatch):
    calls = []
    pages = {"/library": _SEARCH_HTML, "/library/qwen3": _TAG_HTML,
             "/library/deepseek-r1": _TAG_HTML}
    monkeypatch.setattr(L.urllib.request, "urlopen", _fake_urlopen(pages, calls))
    hw = L.HardwareBudget(ram_total_gb=16.0, ram_free_gb=8.0, gpu_name=None,
                          vram_total_gb=None, vram_free_gb=None, source="ram-only",
                          cpu_cores_physical=4)
    results = L.available_models(hw, query="", limit=3)
    names = [r.name for r in results]
    assert "qwen3:0.6b" in names
    assert all(results[i].score >= results[i + 1].score for i in range(len(results) - 1))
    assert all(isinstance(r, L.ModelScore) for r in results)


def test_available_models_dedupes_families(monkeypatch):
    """A family's many tags shouldn't crowd out the whole top-N -- one best
    tag per family keeps the list diverse (llm-checker shows one pick per
    model family too)."""
    r1_tags = _TAG_HTML.replace("qwen3:", "deepseek-r1:")
    pages = {"/library": _SEARCH_HTML, "/library/qwen3": _TAG_HTML,
             "/library/deepseek-r1": r1_tags}
    monkeypatch.setattr(L.urllib.request, "urlopen", _fake_urlopen(pages, []))
    hw = L.HardwareBudget(ram_total_gb=16.0, ram_free_gb=8.0, gpu_name=None,
                          vram_total_gb=None, vram_free_gb=None, source="ram-only")
    results = L.available_models(hw, query="", limit=8)
    families = [r.name.split(":")[0] for r in results]
    assert len(families) == len(set(families))


def test_available_models_skips_embedding_families(monkeypatch):
    """Embedding models (nomic-embed-text etc.) can't chat -- they must not
    crowd out chat models from the recommendation list."""
    pages = {"/library": _SEARCH_HTML, "/library/qwen3": _TAG_HTML,
             "/library/deepseek-r1": _EMBEDDING_TAG_HTML}
    monkeypatch.setattr(L.urllib.request, "urlopen", _fake_urlopen(pages, []))
    hw = L.HardwareBudget(ram_total_gb=16.0, ram_free_gb=8.0, gpu_name=None,
                          vram_total_gb=None, vram_free_gb=None, source="ram-only")
    results = L.available_models(hw, query="", limit=8)
    names = [r.name for r in results]
    assert not any("deepseek-r1" in n for n in names)
    assert any("qwen3" in n for n in names)


def test_available_models_scans_past_24_families_for_ten_compatible(monkeypatch):
    """/local available must list at least 10 Compatible models -- if the
    popular families are all too big for this machine, keep scanning further
    down the library instead of returning Poor entries."""
    big = _TAG_HTML.replace("qwen3:", "bigfam:").replace("523MB", "20GB").replace("1.4GB", "40GB")
    small = _TAG_HTML.replace("qwen3:", "smallfam:")
    lib_html = "".join(f'<a href="/library/fam{i:02d}" class="group w-full">' for i in range(48))
    pages = {"/library": lib_html}
    for i in range(48):
        pages[f"/library/fam{i:02d}"] = big if i < 24 else small
    monkeypatch.setattr(L.urllib.request, "urlopen", _fake_urlopen(pages, []))
    hw = L.HardwareBudget(ram_total_gb=16.0, ram_free_gb=8.0, gpu_name=None,
                          vram_total_gb=None, vram_free_gb=None, source="ram-only")
    results = L.available_models(hw, query="", limit=10)
    assert len(results) >= 10
    assert all(r.category == "Compatible" for r in results)


def test_available_models_no_query_browses_full_library(monkeypatch):
    """Bare /local available must browse the full /library catalog (all
    families by popularity), not the /search trending page."""
    pages = {"/library": _SEARCH_HTML, "/library/qwen3": _TAG_HTML,
             "/library/deepseek-r1": _TAG_HTML}
    calls = []
    monkeypatch.setattr(L.urllib.request, "urlopen", _fake_urlopen(pages, calls))
    hw = L.HardwareBudget(ram_total_gb=16.0, ram_free_gb=8.0, gpu_name=None,
                          vram_total_gb=None, vram_free_gb=None, source="ram-only")
    results = L.available_models(hw, query="", limit=4)
    assert any("ollama.com/library" in url and "q=" not in url for url in calls)
    names = [r.name for r in results]
    assert "qwen3:0.6b" in names


def test_available_models_query_filters(monkeypatch):
    pages = {"/search?q=coder": _SEARCH_HTML, "/library/qwen3": _TAG_HTML}
    calls = []
    monkeypatch.setattr(L.urllib.request, "urlopen", _fake_urlopen(pages, calls))
    hw = L.HardwareBudget(ram_total_gb=16.0, ram_free_gb=8.0, gpu_name=None,
                          vram_total_gb=None, vram_free_gb=None, source="ram-only")
    L.available_models(hw, query="coder", limit=3)
    assert any("q=coder" in url for url in calls)