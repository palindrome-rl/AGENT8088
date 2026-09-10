"""Routing overhead must be reportable, and a model switch must be visible.

Two gaps found by testing the LLMRouter integration against the Tier 1 work
rather than on its own:

1. Routing decisions went only to the application log, so `/cost` -- which
   exists to answer "what did that turn cost" -- reported the ~375ms per turn
   the router adds as nothing at all.

2. `_stable_prefix_hash` was keyed on (prompt, tools) with no model input.
   Provider prompt caches are per model, so a router that picks a different
   model each turn discards the cache while the metric still reports one
   stable prefix. The measurement said "cache-friendly" precisely when it had
   stopped being true.
"""
import json


def test_a_routing_decision_reaches_the_telemetry_file(engine, tmp_path, monkeypatch):
    path = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_PATH", path)

    engine.record_routing_decision(
        {"backend": "llmrouter", "mode": "enabled", "reason": "selected",
         "selected": "ollama-cloud:glm-5.3", "applied": True, "latency_ms": 375})

    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows, "nothing was written"
    row = rows[-1]
    assert row["event"] == "routing_decision"
    assert row["latency_ms"] == 375
    assert row["applied"] is True


def test_the_cost_reader_reports_routing_overhead(tmp_path):
    from agent8088 import efficiency
    path = tmp_path / "t.jsonl"
    rows = [
        {"event": "model_call", "input_tokens": 10, "output_tokens": 5,
         "cost_usd": 0.01, "latency_ms": 900},
        {"event": "routing_decision", "latency_ms": 375, "applied": True},
        {"event": "routing_decision", "latency_ms": 360, "applied": True},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    report = efficiency.telemetry_summary(path)
    assert report["routing_decisions"] == 2, "routing decisions were not counted"
    assert report["routing_latency_ms"] == 735, (
        "the time the router costs is still invisible to /cost")
    assert report["calls"] == 1, "a routing decision must not count as a model call"


def test_the_prefix_hash_changes_when_the_model_changes(engine):
    """Same prompt and tools, different model: the cache is not shared."""
    a = engine._stable_prefix_hash("SYS", [{"function": {"name": "read_text"}}],
                                   model="glm-5.3")
    b = engine._stable_prefix_hash("SYS", [{"function": {"name": "read_text"}}],
                                   model="qwen3.5:397b")
    assert a != b, (
        "the prefix fingerprint ignores the model, so a router switching models "
        "reports a stable prefix while discarding the provider cache")


def test_the_prefix_hash_is_still_stable_for_one_model(engine):
    a = engine._stable_prefix_hash("SYS", [{"function": {"name": "x"}}], model="m")
    b = engine._stable_prefix_hash("SYS", [{"function": {"name": "x"}}], model="m")
    assert a == b, "the fingerprint must stay stable when nothing changed"
