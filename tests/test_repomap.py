import pytest

from agent8088 import repomap

pytest.importorskip("tree_sitter_language_pack")


def _write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_language_for_maps_known_suffixes_and_ignores_the_rest():
    assert repomap.language_for("pkg/thing.py") == "python"
    assert repomap.language_for("main.go") == "go"
    assert repomap.language_for("notes.txt") is None
    assert repomap.language_for("archive.tar.gz") is None


def test_extract_tags_finds_definitions_and_references():
    source = "class Engine:\n    def run(self):\n        return helper()\n"
    tags = repomap.extract_tags("a.py", source)
    defs = {t.name for t in tags if t.kind == "def"}
    refs = {t.name for t in tags if t.kind == "ref"}
    assert {"Engine", "run"} <= defs
    assert "helper" in refs


@pytest.mark.parametrize(("path", "source", "relation"), [
    ("a.py", "def helper():\n    pass\n\nhelper()\n", "call"),
    ("Child.java", "class Child extends Parent {}\n", "inherit"),
    ("Child.cs", "class Child : Parent {}\n", "inherit"),
    ("main.go", 'package main\nimport "core"\nfunc main() {}\n', "module"),
])
def test_extract_tags_records_reference_relation(path, source, relation):
    tags = repomap.extract_tags(path, source)
    assert any(tag.kind == "ref" and tag.relation == relation for tag in tags)


def test_extract_tags_records_the_definition_line():
    source = "x = 1\n\n\ndef target():\n    pass\n"
    tags = repomap.extract_tags("a.py", source)
    target = next(t for t in tags if t.name == "target" and t.kind == "def")
    assert target.line == 4


def test_extract_tags_records_what_kind_of_symbol_was_defined():
    tags = repomap.extract_tags("a.py", "class Engine:\n    pass\n\n\ndef helper():\n    pass\n")
    kinds = {t.name: t.symbol for t in tags if t.kind == "def"}
    assert kinds["Engine"] == "class"
    assert kinds["helper"] == "function"


def test_extract_tags_returns_nothing_for_an_unsupported_language():
    assert repomap.extract_tags("notes.txt", "hello") == []


def test_extract_tags_survives_a_syntactically_broken_file():
    assert isinstance(repomap.extract_tags("a.py", "def ("), list)


def test_pagerank_ranks_a_widely_referenced_node_above_a_lonely_one():
    edges = [("a", "hub", 1.0), ("b", "hub", 1.0), ("c", "hub", 1.0)]
    ranks = repomap.pagerank(["a", "b", "c", "hub", "island"], edges)
    assert ranks["hub"] > ranks["a"]
    assert ranks["hub"] > ranks["island"]


def test_pagerank_returns_a_normalised_distribution():
    ranks = repomap.pagerank(["a", "b"], [("a", "b", 1.0)])
    assert pytest.approx(sum(ranks.values()), abs=1e-6) == 1.0


def test_pagerank_of_an_empty_graph_is_empty():
    assert repomap.pagerank([], []) == {}


def test_rank_symbols_puts_a_referenced_definition_first(tmp_path):
    _write(tmp_path, "core.py", "def widely_used():\n    return 1\n\n\ndef ignored_here():\n    return 2\n")
    _write(tmp_path, "one.py", "from core import widely_used\n\nwidely_used()\n")
    _write(tmp_path, "two.py", "from core import widely_used\n\nwidely_used()\n")
    ranked = repomap.rank_symbols(repomap.collect_tags(tmp_path))
    names = [name for _, name in ranked]
    assert "widely_used" in names
    assert names.index("widely_used") < names.index("ignored_here")


def test_rank_symbols_boosts_focus_files(tmp_path):
    _write(tmp_path, "core.py", "def alpha():\n    return 1\n")
    _write(tmp_path, "side.py", "def beta():\n    return 2\n")
    _write(tmp_path, "user.py", "alpha()\nbeta()\n")
    tags = repomap.collect_tags(tmp_path)
    plain = repomap.rank_symbols(tags)
    focused = repomap.rank_symbols(tags, focus=["side.py"])
    beta_rank = [n for _, n in focused].index("beta")
    assert beta_rank <= [n for _, n in plain].index("beta")


def test_rank_symbols_demotes_a_name_that_half_the_repository_defines(tmp_path):
    # File names are chosen so the alphabetical tie-break favours the WRONG
    # answer: only real scoring can put reconcile_ledger first.
    for index in range(8):
        _write(tmp_path, f"aaa{index}.py", "def get():\n    return 1\n")
    _write(tmp_path, "zzz_ledger.py", "def reconcile_ledger():\n    return 2\n")
    callers = "get()\n" * 8 + "reconcile_ledger()\n" * 8
    _write(tmp_path, "user.py", callers)
    names = [name for _, name in repomap.rank_symbols(repomap.collect_tags(tmp_path))]
    assert names.index("reconcile_ledger") < names.index("get")


def test_rank_symbols_prefers_a_class_over_an_equally_referenced_function(tmp_path):
    _write(tmp_path, "zzz_types.py", "class SettlementRecord:\n    pass\n")
    _write(tmp_path, "aaa_helpers.py", "def format_amount():\n    return 1\n")
    _write(tmp_path, "user.py", "SettlementRecord()\nformat_amount()\n")
    names = [name for _, name in repomap.rank_symbols(repomap.collect_tags(tmp_path))]
    assert names.index("SettlementRecord") < names.index("format_amount")


def test_rank_symbols_prefers_calls_to_imports():
    tags = [
        repomap.Tag("core.py", 1, "call_target", "def", "function"),
        repomap.Tag("core.py", 2, "import_target", "def", "function"),
        repomap.Tag("user.py", 1, "call_target", "ref", relation="call"),
        repomap.Tag("user.py", 2, "import_target", "ref", relation="module"),
    ]
    names = [name for _, name in repomap.rank_symbols(tags)]
    assert names.index("call_target") < names.index("import_target")


def test_a_long_snake_case_name_is_not_distinctive_when_many_files_define_it(tmp_path):
    # Aider treats any long snake_case name as distinctive. In Python that is
    # simply the naming convention, so it must not outrank a genuinely unique
    # name that several files depend on.
    for index in range(4):
        _write(tmp_path, f"aaa{index}.py", "def handle_request():\n    return 1\n")
    _write(tmp_path, "zzz_core.py", "def settle_ledger():\n    return 2\n")
    _write(tmp_path, "user.py", "handle_request()\nsettle_ledger()\n")
    names = [name for _, name in repomap.rank_symbols(repomap.collect_tags(tmp_path))]
    assert names.index("settle_ledger") < names.index("handle_request")


def test_rank_symbols_ranks_test_files_below_source(tmp_path):
    _write(tmp_path, "zzz_core.py", "def compute_total():\n    return 1\n")
    _write(tmp_path, "aaa_tests/test_core.py", "def make_fixture_rows():\n    return []\n")
    _write(tmp_path, "user.py", "compute_total()\nmake_fixture_rows()\n")
    names = [name for _, name in repomap.rank_symbols(repomap.collect_tags(tmp_path))]
    assert names.index("compute_total") < names.index("make_fixture_rows")


def test_collect_tags_skips_the_git_directory(tmp_path):
    _write(tmp_path, "keep.py", "def keep_me():\n    pass\n")
    _write(tmp_path, ".git/hooks/hidden.py", "def hidden_thing():\n    pass\n")
    names = {t.name for t in repomap.collect_tags(tmp_path)}
    assert "keep_me" in names
    assert "hidden_thing" not in names


def test_collect_tags_honours_gitignore(tmp_path):
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "keep.py", "def keep_me():\n    pass\n")
    _write(tmp_path, "build/gen.py", "def generated_thing():\n    pass\n")
    names = {t.name for t in repomap.collect_tags(tmp_path)}
    assert "keep_me" in names
    assert "generated_thing" not in names


def test_collect_tags_skips_virtualenvs_whatever_they_are_named(tmp_path):
    _write(tmp_path, "keep.py", "def keep_me():\n    pass\n")
    _write(tmp_path, ".venv-probe/pyvenv.cfg", "home = /usr\n")
    _write(tmp_path, ".venv-probe/lib/site-packages/dep.py", "def vendored_thing():\n    pass\n")
    names = {t.name for t in repomap.collect_tags(tmp_path)}
    assert "keep_me" in names
    assert "vendored_thing" not in names


def test_collect_tags_skips_site_packages_anywhere(tmp_path):
    _write(tmp_path, "keep.py", "def keep_me():\n    pass\n")
    _write(tmp_path, "libs/site-packages/dep.py", "def vendored_thing():\n    pass\n")
    names = {t.name for t in repomap.collect_tags(tmp_path)}
    assert "keep_me" in names
    assert "vendored_thing" not in names


def test_build_map_renders_paths_and_definition_lines(tmp_path):
    _write(tmp_path, "core.py", "class Engine:\n    def run(self):\n        return 1\n")
    _write(tmp_path, "user.py", "from core import Engine\n\nEngine().run()\n")
    rendered = repomap.build_map(tmp_path, budget_tokens=500)
    assert "core.py" in rendered
    assert "class Engine" in rendered


def test_build_map_respects_the_token_budget(tmp_path):
    for index in range(40):
        _write(tmp_path, f"mod{index}.py",
               "".join(f"def function_number_{index}_{n}():\n    return {n}\n\n" for n in range(20)))
    rendered = repomap.build_map(tmp_path, budget_tokens=200)
    assert len(rendered) // 4 <= 200


def test_build_map_minimal_shows_one_symbol_per_file(tmp_path):
    _write(tmp_path, "core.py", "def alpha():\n    pass\n\n\ndef beta():\n    pass\n")
    _write(tmp_path, "user.py", "alpha()\nbeta()\n")
    standard = repomap.build_map(tmp_path, budget_tokens=500)
    minimal = repomap.build_map(tmp_path, budget_tokens=500, detail_level="minimal")
    assert "def alpha" in standard and "def beta" in standard
    assert sum(name in minimal for name in ("def alpha", "def beta")) == 1


def test_build_map_of_an_empty_repository_says_so(tmp_path):
    rendered = repomap.build_map(tmp_path, budget_tokens=100)
    assert "no source files" in rendered.lower()


def test_build_map_is_deterministic(tmp_path):
    _write(tmp_path, "core.py", "def alpha():\n    pass\n\n\ndef beta():\n    pass\n")
    _write(tmp_path, "user.py", "alpha()\n")
    assert repomap.build_map(tmp_path, budget_tokens=300) == repomap.build_map(tmp_path, budget_tokens=300)
