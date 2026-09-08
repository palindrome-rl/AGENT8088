"""Auto model routing: chain parsing, ordering, cooldowns, rung selection,
and the struggle-signal trigger.

The regression that matters most is the last test: with any explicit model
selected, routing must be completely inert.
"""
import time

import pytest

from agent8088 import routing


# --- selector recognition --------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("auto", True), ("auto:fast", True), ("auto:smart", True), ("AUTO", True),
    ("gpt-4o", False), ("", False), (None, False), ("automatic", False),
])
def test_is_auto(name, expected):
    assert routing.is_auto(name) is expected


@pytest.mark.parametrize("name,expected", [
    ("auto", ""), ("auto:fast", "fast"), ("auto:smart", "smart"),
    ("auto:bogus", ""),          # unknown variant degrades to the default
])
def test_variant(name, expected):
    assert routing.variant(name) == expected


# --- chain parsing ---------------------------------------------------------
def test_parse_chain_orders_and_filters():
    known = {"ollama", "groq", "openai"}
    chain = routing.parse_chain("ollama:a, groq:b , openai:c", known)
    assert chain == [("ollama", "a"), ("groq", "b"), ("openai", "c")]


def test_parse_chain_drops_unknown_provider():
    # A chain naming a provider the user has since removed must degrade, not brick.
    assert routing.parse_chain("ghost:x,ollama:a", {"ollama"}) == [("ollama", "a")]


def test_parse_chain_drops_malformed_and_duplicates():
    assert routing.parse_chain("noseparator,ollama:,:model,ollama:a,ollama:a",
                               {"ollama"}) == [("ollama", "a")]


def test_parse_chain_empty():
    assert routing.parse_chain("", {"ollama"}) == []
    assert routing.parse_chain(None, {"ollama"}) == []


# --- starting rung ---------------------------------------------------------
def test_starting_rung_defaults_cheap():
    assert routing.starting_rung("auto", 3) == 0
    assert routing.starting_rung("auto:fast", 3) == 0


def test_starting_rung_smart_starts_strong():
    assert routing.starting_rung("auto:smart", 3) == 2


def test_starting_rung_empty_chain():
    assert routing.starting_rung("auto:smart", 0) == 0


# --- seed ordering ---------------------------------------------------------
def test_order_for_seed_is_weakest_first():
    ordered = routing.order_for_seed([
        ("a", "claude-opus-4"), ("b", "llama-3.1-8b-instant"), ("c", "gpt-4o"),
    ])
    assert ordered[0] == ("b", "llama-3.1-8b-instant")
    assert ordered[-1] == ("a", "claude-opus-4")


def test_order_for_seed_ties_keep_discovery_order():
    # Two unknown ids score equally; the user's provider order must survive.
    ordered = routing.order_for_seed([("a", "mystery-one"), ("b", "mystery-two")])
    assert ordered == [("a", "mystery-one"), ("b", "mystery-two")]


# --- cooldowns -------------------------------------------------------------
def test_cooldown_marks_and_expires():
    routing.clear_cooldowns()
    routing.mark_cooldown("groq", "m", 30)
    assert routing.cooling("groq", "m") is True
    # A cooldown is a claim about *now*; past its window it must clear itself.
    assert routing.cooling("groq", "m", now=time.time() + 31) is False
    assert routing.cooling("groq", "m") is False


def test_cooldown_ignores_nonpositive():
    routing.clear_cooldowns()
    routing.mark_cooldown("groq", "m", 0)
    assert routing.cooling("groq", "m") is False


def test_param_count_score_maps_size_to_tier():
    assert routing._param_count_score("some-model-120b") == 90
    assert routing._param_count_score("qwen2.5-coder:32b") == 70
    assert routing._param_count_score("llama-3.1-8b-instant") == 30
    assert routing._param_count_score("tiny-1.5b") == 15


def test_param_count_score_none_when_no_count_present():
    assert routing._param_count_score("gpt-4o-mini") is None
    assert routing._param_count_score("mistral-small-2603") is None


def test_keyword_score_matches_existing_tiers():
    assert routing._keyword_score("claude-opus-4") == 100
    assert routing._keyword_score("gemini-2.0-flash") == 20


def test_keyword_score_none_when_nothing_matches():
    assert routing._keyword_score("some-totally-unknown-id") is None


def test_rank_hint_prefers_the_catalog_over_the_heuristic():
    # gemini:gemini-2.5-pro is rank 85 in the bundled catalog (Task 1). If the
    # heuristic ran instead, "flash"/"pro" keyword matching would give a very
    # different number -- this proves the catalog wins.
    assert routing.rank_hint("gemini", "gemini-2.5-pro") == 85


def test_rank_hint_falls_back_to_the_heuristic_for_an_unlisted_model():
    # Not in the catalog. Falls through to the param-count + keyword combo
    # from Task 3: "70b" -> 70 beats no keyword match.
    assert routing.rank_hint("some-custom-provider", "mystery-model-70b") == 70


def test_rank_hint_uses_the_shared_default_when_nothing_matches_at_all():
    assert routing.rank_hint("some-custom-provider", "totally-opaque-id") == 50


def test_order_for_seed_still_works_with_the_new_two_argument_rank_hint():
    ordered = routing.order_for_seed([
        ("a", "claude-opus-4"), ("b", "llama-3.1-8b-instant"), ("c", "gpt-4o"),
    ])
    assert ordered[0] == ("b", "llama-3.1-8b-instant")
    assert ordered[-1] == ("a", "claude-opus-4")


# --- selection -------------------------------------------------------------
CHAIN = [("ollama", "a"), ("groq", "b"), ("openai", "c")]


def test_select_returns_requested_rung():
    routing.clear_cooldowns()
    assert routing.select(CHAIN, 0) == (0, "ollama", "a")
    assert routing.select(CHAIN, 2) == (2, "openai", "c")


def test_select_skips_cooling_rung():
    routing.clear_cooldowns()
    routing.mark_cooldown("ollama", "a", 60)
    assert routing.select(CHAIN, 0) == (1, "groq", "b")


def test_select_skips_rung_that_cannot_hold_context():
    routing.clear_cooldowns()
    # The guard that stops escalation landing on a smaller-window model.
    assert routing.select(CHAIN, 0, fits=lambda p, m: p == "openai") == (2, "openai", "c")


def test_select_wraps_when_nothing_above_qualifies():
    routing.clear_cooldowns()
    routing.mark_cooldown("openai", "c", 60)
    # Top rung cooling must not strand the turn with no model at all.
    assert routing.select(CHAIN, 2) == (0, "ollama", "a")


def test_select_returns_none_when_nothing_qualifies():
    routing.clear_cooldowns()
    assert routing.select(CHAIN, 0, fits=lambda p, m: False) is None
    assert routing.select([], 0) is None


# --- the trigger -----------------------------------------------------------
def test_quality_failure_signals():
    assert routing.quality_failure(length_retries=1) == "length_cutoff"
    assert routing.quality_failure(parse_error_retries=2) == "invalid_tool_calls"
    assert routing.quality_failure(parse_error_retries=1,
                                   missing_args_retries=1) == "invalid_tool_calls"
    assert routing.quality_failure(unknown_retries=2) == "unknown_tools"
    assert routing.quality_failure(forcing=True) == "no_progress"


def test_quality_failure_stays_quiet_below_threshold():
    assert routing.quality_failure() == ""
    assert routing.quality_failure(parse_error_retries=1) == ""
    assert routing.quality_failure(unknown_retries=1) == ""


# --- integration: the loop actually climbs ---------------------------------
def _length_response(content):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content})(),
        "finish_reason": "length",
    })()]})()


def _stop_response(content):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content})(),
        "finish_reason": "stop",
    })()]})()


def _two_rung_auto(monkeypatch, engine):
    engine._auto_chain = [("p_cheap", "cheap"), ("p_strong", "strong")]
    monkeypatch.setattr(engine, "MODEL_NAME", "auto")
    monkeypatch.setattr(engine, "get_client", lambda name=None: (object(), "ignored"))
    # Generous window everywhere so the context-fit guard never decides the outcome.
    monkeypatch.setattr(engine, "_active_model_token_limits",
                        lambda p="", m="": (1_000_000, 4096))
    routing.clear_cooldowns()


def test_auto_climbs_one_rung_after_a_length_cutoff(monkeypatch, engine):
    """A truncated response is evidence the turn is failing -> escalate once."""
    _two_rung_auto(monkeypatch, engine)
    seen = []

    def _fake(messages, tools, max_tokens=None, **kw):
        seen.append((kw.get("provider_override"), kw.get("model_override")))
        return _length_response("x" * 500) if len(seen) == 1 else _stop_response("done")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    trace = []
    result = engine.run_agent([{"role": "user", "content": "hard one"}],
                              max_turns=5, trace=trace)

    assert result == "done"
    assert seen[0] == ("p_cheap", "cheap"), "first attempt must start cheap"
    assert seen[1] == ("p_strong", "strong"), "after a length cutoff it must climb"
    escalations = [e for e in trace if e.get("type") == "model_escalation"]
    assert len(escalations) == 1
    assert escalations[0]["reason"] == "length_cutoff"


def test_auto_stays_put_when_the_turn_goes_fine(monkeypatch, engine):
    """No struggle signal, no escalation — and so no surprise bill."""
    _two_rung_auto(monkeypatch, engine)
    seen = []

    def _fake(messages, tools, max_tokens=None, **kw):
        seen.append((kw.get("provider_override"), kw.get("model_override")))
        return _stop_response("fine")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    trace = []
    assert engine.run_agent([{"role": "user", "content": "easy"}],
                            max_turns=5, trace=trace) == "fine"
    assert seen == [("p_cheap", "cheap")]
    assert not [e for e in trace if e.get("type") == "model_escalation"]


def test_auto_smart_starts_at_the_top_rung(monkeypatch, engine):
    """`auto:smart` skips the cheap attempt the user already expects to fail."""
    _two_rung_auto(monkeypatch, engine)
    monkeypatch.setattr(engine, "MODEL_NAME", "auto:smart")
    seen = []

    def _fake(messages, tools, max_tokens=None, **kw):
        seen.append((kw.get("provider_override"), kw.get("model_override")))
        return _stop_response("fine")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    engine.run_agent([{"role": "user", "content": "known-hard"}], max_turns=5)
    assert seen == [("p_strong", "strong")]


def test_auto_climbs_past_a_dead_model_within_the_same_turn(monkeypatch, engine):
    """Reproduces the real failure: rung 1's model id is renamed/deprecated by
    the provider (a 404, not a timeout/429/5xx), so it is NOT retryable and
    would otherwise abort the whole turn with no chance to try another rung.
    """
    _two_rung_auto(monkeypatch, engine)
    seen = []

    def _fake(messages, tools, max_tokens=None, **kw):
        target = (kw.get("provider_override"), kw.get("model_override"))
        seen.append(target)
        if target == ("p_cheap", "cheap"):
            raise RuntimeError("404 - model no longer available")
        return _stop_response("done")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    trace = []
    result = engine.run_agent([{"role": "user", "content": "hi"}],
                              max_turns=5, trace=trace)

    assert result == "done", "must recover within the same turn, not abort it"
    assert seen == [("p_cheap", "cheap"), ("p_strong", "strong")]
    reasons = [e["reason"] for e in trace if e.get("type") == "model_escalation"]
    assert reasons == ["hard_error"]
    assert routing.cooling("p_cheap", "cheap"), "the dead model must be cooled off"


def test_auto_gives_up_cleanly_when_every_rung_is_dead(monkeypatch, engine):
    """No rung works -> a real answer, not a crash, and no infinite loop."""
    _two_rung_auto(monkeypatch, engine)

    def _always_fails(messages, tools, max_tokens=None, **kw):
        raise RuntimeError("404 - model no longer available")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _always_fails)
    result = engine.run_agent([{"role": "user", "content": "hi"}], max_turns=5)
    assert "backend errored" in result.lower()


def test_explicit_model_hard_error_is_unaffected_by_auto_recovery(monkeypatch, engine):
    """The hard-error recovery path itself must also be gated on `auto` --
    an explicit model that dies must fail exactly as it always has."""
    _two_rung_auto(monkeypatch, engine)
    monkeypatch.setattr(engine, "MODEL_NAME", "gpt-4o")

    def _always_fails(messages, tools, max_tokens=None, **kw):
        raise RuntimeError("404 - model no longer available")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _always_fails)
    trace = []
    result = engine.run_agent([{"role": "user", "content": "hi"}],
                              max_turns=5, trace=trace)
    assert "backend errored" in result.lower()
    assert not [e for e in trace if e.get("type") == "model_escalation"]


# --- regression: inert unless opted in -------------------------------------
def test_explicit_model_is_never_routed(monkeypatch, engine):
    """The whole feature must be invisible to a user who never types `auto`.

    Even with a chain configured, an explicit model must reach the completion
    call with no provider/model override applied. This is the guarantee that
    existing users see no behaviour change at all.
    """
    _two_rung_auto(monkeypatch, engine)
    monkeypatch.setattr(engine, "MODEL_NAME", "gpt-4o")
    seen = []

    def _fake(messages, tools, max_tokens=None, **kw):
        seen.append((kw.get("provider_override"), kw.get("model_override")))
        return _length_response("x" * 500) if len(seen) == 1 else _stop_response("done")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    trace = []
    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=5, trace=trace)

    # A length cutoff happened, which WOULD have escalated under `auto`.
    assert len(seen) == 2
    assert {p for p, _ in seen} == {None}, "explicit model must not be re-routed"
    assert not [e for e in trace if e.get("type") == "model_escalation"]
