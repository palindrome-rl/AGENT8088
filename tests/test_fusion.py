"""Black-box tests for fusion.py: discover_panel, run_panel, judge, run_fusion.

fusion.py is treated as untrusted -- every assertion is derived from reading
its actual source, not from what its docstrings claim it does.
"""
import time
from types import SimpleNamespace

import pytest

from agent8088 import fusion


def _fake_response(text, input_tokens=10, output_tokens=20):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens),
    )


def _provider(model, base_url, api_key="key"):
    return {"model": model, "base_url": base_url, "api_key": api_key}


def _patch_discovery(monkeypatch, engine):
    """Make discover_panel's key check and client construction deterministic
    and independent of real config / env / network."""
    monkeypatch.setattr(engine, "_provider_api_key", lambda info: info.get("api_key", ""))
    monkeypatch.setattr(
        engine, "get_client",
        lambda name: (SimpleNamespace(name=name), engine.PROVIDERS[name]["model"]),
    )


# ---------------------------------------------------------------------------
# discover_panel
# ---------------------------------------------------------------------------

def test_discover_panel_dedupes_identical_backend(monkeypatch, engine):
    engine.PROVIDERS = {
        "p1": _provider("m1", "http://same"),
        "p2": _provider("m1", "http://same"),
    }
    _patch_discovery(monkeypatch, engine)
    panel = fusion.discover_panel()
    assert len(panel) == 1
    assert panel[0].provider == "p1"


def test_discover_panel_excludes_provider_without_working_key(monkeypatch, engine):
    engine.PROVIDERS = {
        "nokey": _provider("m1", "http://a", api_key=""),
        "haskey": _provider("m2", "http://b", api_key="k"),
    }
    _patch_discovery(monkeypatch, engine)
    panel = fusion.discover_panel()
    assert [m.provider for m in panel] == ["haskey"]


def test_discover_panel_caps_to_max_panel_size(monkeypatch, engine):
    engine.PROVIDERS = {
        f"p{i}": _provider(f"m{i}", f"http://host{i}") for i in range(5)
    }
    _patch_discovery(monkeypatch, engine)
    panel = fusion.discover_panel(max_panel_size=3)
    assert len(panel) == 3
    assert [m.provider for m in panel] == ["p0", "p1", "p2"]


# ---------------------------------------------------------------------------
# build_explicit_panel
# ---------------------------------------------------------------------------

def test_build_explicit_panel_uses_specified_model(monkeypatch, engine):
    engine.PROVIDERS = {"gemini": _provider("gemini-2.5-flash", "http://g")}
    _patch_discovery(monkeypatch, engine)
    panel = fusion.build_explicit_panel(["gemini:gemini-3-pro"])
    assert len(panel) == 1
    assert panel[0].provider == "gemini"
    assert panel[0].model == "gemini-3-pro"


def test_build_explicit_panel_bare_provider_uses_its_default_model(monkeypatch, engine):
    engine.PROVIDERS = {"gemini": _provider("gemini-2.5-flash", "http://g")}
    _patch_discovery(monkeypatch, engine)
    panel = fusion.build_explicit_panel(["gemini"])
    assert panel[0].model == "gemini-2.5-flash"


def test_build_explicit_panel_unknown_provider_raises(monkeypatch, engine):
    engine.PROVIDERS = {"gemini": _provider("gemini-2.5-flash", "http://g")}
    _patch_discovery(monkeypatch, engine)
    with pytest.raises(ValueError, match="unknown provider 'nope'"):
        fusion.build_explicit_panel(["nope:some-model"])


def test_build_explicit_panel_no_key_raises(monkeypatch, engine):
    engine.PROVIDERS = {"gemini": _provider("gemini-2.5-flash", "http://g", api_key="")}
    _patch_discovery(monkeypatch, engine)
    with pytest.raises(ValueError, match="no working API key"):
        fusion.build_explicit_panel(["gemini:gemini-3-pro"])


def test_build_explicit_panel_colon_in_model_id_preserved(monkeypatch, engine):
    engine.PROVIDERS = {"ollama-cloud": _provider("glm-5.3", "http://o")}
    _patch_discovery(monkeypatch, engine)
    panel = fusion.build_explicit_panel(["ollama-cloud:gpt-oss:120b"])
    assert panel[0].model == "gpt-oss:120b"


# ---------------------------------------------------------------------------
# run_fusion (end to end, discover_panel bypassed with a fixed fake panel)
# ---------------------------------------------------------------------------

def _fixed_panel(names):
    return [fusion.PanelMember(provider=n, model=f"{n}-model", client=SimpleNamespace(name=n))
            for n in names]


def test_run_fusion_normal_n_way_with_working_judge(monkeypatch, engine):
    panel = _fixed_panel(["p1", "p2", "p3"])
    monkeypatch.setattr(fusion, "discover_panel", lambda max_panel_size=6: panel)
    # disable actual shuffling so blind label order == original panel order
    monkeypatch.setattr(fusion.random, "shuffle", lambda seq: None)
    monkeypatch.setattr(engine, "get_client", lambda name: (SimpleNamespace(name=name), "judge-model"))

    texts = {"p1": "Answer from p1", "p2": "Answer from p2", "p3": "Answer from p3"}
    calls = []

    def fake_completion(client, messages, tools, **kw):
        provider = kw["provider_name"]
        calls.append(provider)
        if provider == "judge":
            return _fake_response("WINNER: B\nVERDICT: it is more thorough.",
                                   input_tokens=5, output_tokens=7)
        return _fake_response(texts[provider], input_tokens=1, output_tokens=2)

    result = fusion.run_fusion("What is 2+2?", judge_provider="judge",
                                completion_fn=fake_completion)

    assert result.judge_parsed is True
    # label B (no shuffle) == original index 1 == "p2"
    assert result.winner_answer == "Answer from p2"
    assert result.total_input_tokens == 1 + 1 + 1 + 5
    assert result.total_output_tokens == 2 + 2 + 2 + 7
    assert calls.count("judge") == 1


def test_run_fusion_one_panel_member_raises(monkeypatch, engine):
    panel = _fixed_panel(["p1", "p2", "p3"])
    monkeypatch.setattr(fusion, "discover_panel", lambda max_panel_size=6: panel)
    monkeypatch.setattr(fusion.random, "shuffle", lambda seq: None)
    monkeypatch.setattr(engine, "get_client", lambda name: (SimpleNamespace(name=name), "judge-model"))

    def fake_completion(client, messages, tools, **kw):
        provider = kw["provider_name"]
        if provider == "p2":
            raise RuntimeError("boom: provider unreachable")
        if provider == "judge":
            return _fake_response("WINNER: A\nVERDICT: fine.")
        return _fake_response(f"answer from {provider}")

    result = fusion.run_fusion("q", judge_provider="judge", completion_fn=fake_completion)

    by_provider = {r.member.provider: r for r in result.results}
    assert by_provider["p2"].error is not None
    assert "boom" in by_provider["p2"].error
    assert by_provider["p1"].error is None
    assert by_provider["p3"].error is None
    assert result.winner_index is not None
    assert result.winner_answer in ("answer from p1", "answer from p3")


def test_run_panel_member_times_out(engine):
    panel = _fixed_panel(["slow", "fast"])

    def fake_completion(client, messages, tools, **kw):
        if kw["provider_name"] == "slow":
            time.sleep(0.3)
        else:
            time.sleep(0)
        return _fake_response(f"answer from {kw['provider_name']}")

    results = fusion.run_panel(panel, "q", member_timeout_s=0.05, completion_fn=fake_completion)

    by_provider = {r.member.provider: r for r in results}
    assert by_provider["slow"].error is not None
    assert "Timeout" in by_provider["slow"].error
    assert by_provider["fast"].error is None
    assert by_provider["fast"].text == "answer from fast"


def test_run_fusion_single_survivor_skips_judge(monkeypatch, engine):
    panel = _fixed_panel(["p1", "p2"])
    monkeypatch.setattr(fusion, "discover_panel", lambda max_panel_size=6: panel)

    calls = []

    def fake_completion(client, messages, tools, **kw):
        provider = kw["provider_name"]
        calls.append(provider)
        if provider == "p1":
            raise RuntimeError("fail")
        return _fake_response("only answer")

    # get_client for the judge should never even be reached in this path.
    def _get_client_should_not_be_called(name):
        raise AssertionError("get_client for judge should not be called with a single survivor")

    monkeypatch.setattr(engine, "get_client", _get_client_should_not_be_called)

    result = fusion.run_fusion("q", completion_fn=fake_completion)

    assert result.judge_parsed is False
    assert result.winner_answer == "only answer"
    assert result.winner_index is not None
    assert result.results[result.winner_index].text == "only answer"
    assert "judge" not in calls
    assert calls.count("p2") == 1


def test_run_fusion_zero_survivors(monkeypatch, engine):
    panel = _fixed_panel(["p1", "p2"])
    monkeypatch.setattr(fusion, "discover_panel", lambda max_panel_size=6: panel)

    def fake_completion(client, messages, tools, **kw):
        raise RuntimeError("all dead")

    result = fusion.run_fusion("q", completion_fn=fake_completion)

    assert result.winner_index is None
    assert result.judge_error
    assert "all 2 panel members failed" in result.judge_error


def test_run_fusion_zero_providers_discovered(monkeypatch, engine):
    monkeypatch.setattr(fusion, "discover_panel", lambda max_panel_size=6: [])

    result = fusion.run_fusion("q", completion_fn=lambda *a, **kw: _fake_response("x"))

    assert result.results == []
    assert result.winner_index is None
    assert result.judge_error
    assert "no providers" in result.judge_error


def test_run_fusion_judge_unparseable_falls_back_to_first_survivor(monkeypatch, engine):
    panel = _fixed_panel(["p1", "p2", "p3"])
    monkeypatch.setattr(fusion, "discover_panel", lambda max_panel_size=6: panel)
    monkeypatch.setattr(fusion.random, "shuffle", lambda seq: None)
    monkeypatch.setattr(engine, "get_client", lambda name: (SimpleNamespace(name=name), "judge-model"))

    def fake_completion(client, messages, tools, **kw):
        provider = kw["provider_name"]
        if provider == "judge":
            return _fake_response("I think the second one, honestly, hard to say.")
        return _fake_response(f"answer from {provider}")

    result = fusion.run_fusion("q", judge_provider="judge", completion_fn=fake_completion)

    assert result.judge_parsed is False
    assert result.winner_answer == "answer from p1"
    assert result.winner_index == 0
    assert result.results[result.winner_index].member.provider == "p1"


# ---------------------------------------------------------------------------
# judge() blinding
# ---------------------------------------------------------------------------

def _survivor(provider, model, text):
    member = fusion.PanelMember(provider=provider, model=model, client=SimpleNamespace())
    return fusion.PanelResult(member=member, text=text)


def test_judge_blinding_no_provider_or_model_leakage():
    survivors = [
        _survivor("openai", "gpt-x", "The answer is 4."),
        _survivor("anthropic", "claude-x", "The result is four."),
    ]
    captured = {}

    def fake_completion(client, messages, tools, **kw):
        captured["messages"] = messages
        return _fake_response("WINNER: A\nVERDICT: clearer.")

    fusion.judge("what is 2+2?", survivors, SimpleNamespace(), "judge-model", "judge",
                 completion_fn=fake_completion)

    prompt_text = "\n".join(m["content"] for m in captured["messages"])
    for leaky in ("openai", "anthropic", "gpt-x", "claude-x"):
        assert leaky not in prompt_text
    assert "Answer A" in prompt_text
    assert "Answer B" in prompt_text


def test_judge_blinding_actually_randomizes_order():
    import random as random_mod

    survivors = [
        _survivor("p1", "m1", "text1"),
        _survivor("p2", "m2", "text2"),
        _survivor("p3", "m3", "text3"),
        _survivor("p4", "m4", "text4"),
    ]

    def fake_completion(client, messages, tools, **kw):
        return _fake_response("WINNER: A\nVERDICT: fine.")

    winners = set()
    for seed in range(20):
        rng = random_mod.Random(seed)
        result = fusion.judge("q", survivors, SimpleNamespace(), "m", "judge",
                              completion_fn=fake_completion, rng=rng)
        winners.add(result.winner_index)

    assert len(winners) > 1, "winner_index for label A never changed across 20 seeds"
