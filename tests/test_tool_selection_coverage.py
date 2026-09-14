"""Coverage floor for tool selection.

COMP@K is all-or-nothing per prompt: a task needing four tools fails if any one
is missing. That is the metric that predicts whether a compound task can finish,
and the one ranked selection was silently failing -- 58% per-tool recall looked
survivable while only 40% of tasks were actually completable.

The embedder here is a deterministic stand-in rather than the real one, so the
floor is reproducible in CI where no embedding model has been pulled.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "support"))
from tool_selection_golden import GOLDEN  # noqa: E402


class _Embedder:
    """Keyword-bucketed unit vectors. Crude on purpose: the point is a stable
    semantic leg, not a good one -- if the floor only holds with a strong
    embedder, it is not a floor."""

    _BUCKETS = (
        "hardware model machine local gpu ram",
        "git commit branch repo push clone",
        "file write read text content save",
        "web search page browse url internet",
        "document convert pdf docx markdown",
        "mcp server subagent skill schedule",
    )

    def _vec(self, text):
        low = str(text or "").lower()
        return [float(sum(word in low for word in bucket.split()))
                for bucket in self._BUCKETS]

    def embed(self, texts):
        return [self._vec(text) for text in texts]

    def embed_one(self, query):
        return self._vec(query)


@pytest.fixture
def seeded(engine, monkeypatch):
    monkeypatch.setattr(engine.memory, "embedder", lambda: _Embedder())
    engine._TOOL_INDEX_CACHE.clear()
    return engine


def _select(engine, prompt):
    engine._TOOL_INDEX_CACHE.clear()
    return engine.select_tool_names_for_request(
        prompt, set(engine.TOOL_SPECS), mode="hybrid")


def _comp_at_k(engine):
    covered = sum(not (required - _select(engine, prompt))
                  for _label, prompt, required in GOLDEN)
    return covered / len(GOLDEN)


def test_comp_at_k_floor(seeded):
    """Regression floor. Raise it as phases land; never lower it to pass."""
    score = _comp_at_k(seeded)
    assert score >= 0.70, f"COMP@K regressed to {score:.0%}"


def test_pinned_core_is_never_dropped(seeded):
    """File I/O and shell are needed by so many tasks that ranking them is a
    false economy: a task that loses write_file cannot finish, and the model
    cannot ask for a tool it was never told exists."""
    for label, prompt, _required in GOLDEN:
        chosen = _select(seeded, prompt)
        missing = (seeded._TOOL_SELECTION_PINNED & set(seeded.TOOL_SPECS)) - chosen
        assert not missing, f"pinned core {sorted(missing)} dropped for {label}"


def test_routing_block_only_names_offered_tools(seeded):
    """The mandatory-routing block must not order a call the model cannot make.
    Naming an unadvertised tool sends the model into unknown-tool recovery."""
    offered = {"read_text", "execute_shell"}
    docs = seeded.render_tool_docs(
        {name: seeded.TOOL_SPECS[name] for name in offered}, catalogue=False)
    for absent in ("web_search", "browse_page", "create_document"):
        assert absent not in docs, f"routing block names unoffered tool {absent}"


def test_name_index_lists_every_tool_cheaply(engine):
    """Awareness must not depend on selection. ~180 tokens buys a guarantee that
    the model never reaches for a tool it has not been told exists."""
    index = engine.render_tool_name_index(engine.TOOL_SPECS)
    for name in engine.TOOL_SPECS:
        assert name in index, f"{name} missing from the name index"
    assert len(index) // 4 < 400, f"name index too expensive: ~{len(index) // 4} tokens"


def test_name_index_labels_tool_families(engine):
    """Families make the names-only index scannable without hiding tools."""
    index = engine.render_tool_name_index(engine.TOOL_SPECS)
    assert "- Git: git_branch" in index
    assert "git_status" in index.split("- Git:", 1)[1].split("\n", 1)[0]
    assert "- CLI Anything: cli_anything_info" in index
