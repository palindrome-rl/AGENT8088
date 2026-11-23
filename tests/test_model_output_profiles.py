"""Output limits per model: matched by name wherever the model runs, else by size, else the larger model's limits.

On Qwen3.8-27B the context-scaled cap with the adaptive median sat at 8,192 tokens on 91% of calls in a
long evaluation run, and 4.9% of calls were cut off and thrown away (about 4.6 hours): finished reasoning on that
model often runs past 8K. Gemma 4 12B is the other way round: a reply past about 8K has mostly lost its way.
"""
import pytest

from agent8088 import loop_check
from agent8088 import model_profiles as mp


@pytest.mark.parametrize("name", [
    "qwen3.8-27b", "Qwen3.8-27B", "Qwen/Qwen3.8-27B-AWQ", "qwen3.8:27b", "openai/qwen3.8-27b-instruct",
    "Qwen3.8-27B-Q4_K_M.gguf", "qwen-3.8-27b", "hosted_vllm/Qwen/Qwen3.8-27B-FP8",
])
def test_qwen38_27b_is_known_wherever_it_runs(name):
    found = mp.detect(name)
    assert (found.source, found.matched, found.profile) == ("known", "qwen3.8-27b", mp.QWEN38_27B)


@pytest.mark.parametrize("name", [
    "gemma-4-12b-it", "google/gemma-4-12b-it", "gemma4:12b", "gemma-4-12B-it-Q4_K_M.gguf",
    "unsloth/gemma-4-12b-it-GGUF",
])
def test_gemma4_12b_is_known_wherever_it_runs(name):
    found = mp.detect(name)
    assert (found.source, found.matched, found.profile) == ("known", "gemma-4-12b", mp.GEMMA4_12B)


@pytest.mark.parametrize("name", ["qwen3.6-27b", "qwen/qwen3.8-2.4t-a95b", "gemma-4-31b-it", "gemma-3-12b-it",
                                  "qwen3.8-14b"])
def test_other_versions_and_sizes_are_not_the_known_models(name):
    assert mp.detect(name).source == "size"


@pytest.mark.parametrize("name, size", [
    ("llama-3.1-70b-instruct", 70), ("qwen3-30b-a3b", 30), ("mixtral-8x7b", 56), ("qwen3:0.6b", 0.6),
    ("gemma-3-270m", 0.27), ("phi-4-14B", 14), ("glm-4.5-air-106b-a12b", 106), ("model-1.5b", 1.5),
])
def test_the_size_is_read_from_the_name(name, size):
    assert mp.parameter_count(name) == pytest.approx(size)


@pytest.mark.parametrize("name", ["gpt-4o", "llama3.3", "glm-5.3", "deepseek-v4-flash:0731", "my-finetune", "",
                                  "qwen3.8-2.4", "Q4_K_M"])
def test_a_name_with_no_size_gets_the_larger_models_limits(name):
    found = mp.detect(name)
    assert found.source == "unknown size" and found.size_b is None
    assert found.profile.thinking_cap == mp.QWEN38_27B.thinking_cap
    assert not found.profile.calibrated


@pytest.mark.parametrize("name, profile", [
    ("llama-3.1-70b", mp.QWEN38_27B), ("phi-4-15b", mp.GEMMA4_12B), ("qwen3.5-9b", mp.GEMMA4_12B),
    ("model-15.5b", mp.QWEN38_27B), ("qwen3:0.6b", mp.GEMMA4_12B),
])
def test_above_15b_the_qwen_limits_at_or_below_the_gemma_limits(name, profile):
    found = mp.detect(name)
    assert found.source == "size"
    assert (found.profile.thinking_cap, found.profile.nonthinking_cap, found.profile.loop_sensitivity) == (
        profile.thinking_cap, profile.nonthinking_cap, profile.loop_sensitivity)
    assert not found.profile.calibrated and "uncalibrated" in found.profile.note


def test_the_known_limits():
    assert (mp.QWEN38_27B.thinking_cap, mp.QWEN38_27B.nonthinking_cap) == (20000, 8192)
    assert (mp.GEMMA4_12B.thinking_cap, mp.GEMMA4_12B.nonthinking_cap) == (10000, 8192)
    assert mp.QWEN38_27B.calibrated and mp.GEMMA4_12B.calibrated


def test_the_description_says_how_the_limits_were_found():
    assert "known model qwen3.8-27b" in mp.describe("qwen3.8:27b", mp.detect("qwen3.8:27b"))
    line = mp.describe("my-finetune", mp.detect("my-finetune"))
    assert "no size in the name" in line and "uncalibrated" in line


# ---------------------------------------------------------------------------------------------------- the caps

def test_the_profile_sets_the_caps(engine, monkeypatch):
    for flag in ("_INITIAL_CAP_SET", "_MIN_TOKENS_SET", "_THINKING_OFF_SET"):
        monkeypatch.setattr(engine, flag, False)
    assert engine._output_cap_defaults(65536, mp.QWEN38_27B) == (20000, 8192, 8192)
    assert engine._output_cap_defaults(65536, mp.GEMMA4_12B) == (10000, 8192, 8192)
    # Without a profile the old context-scaled caps stand.
    assert engine._output_cap_defaults(65536) == (16384, 8192, 8192)


def test_the_users_own_caps_win_over_the_profile(engine, monkeypatch):
    monkeypatch.setattr(engine, "_INITIAL_CAP_SET", True)
    monkeypatch.setattr(engine, "INITIAL_COMPLETION_CAP", 12000)
    monkeypatch.setattr(engine, "_MIN_TOKENS_SET", True)
    monkeypatch.setattr(engine, "MAIN_LLM_MIN_TOKENS", 5000)
    monkeypatch.setattr(engine, "_THINKING_OFF_SET", True)
    monkeypatch.setattr(engine, "THINKING_OFF_MAX_TOKENS", 7000)
    assert engine._output_cap_defaults(65536, mp.QWEN38_27B) == (12000, 5000, 7000)


def test_profiles_off_is_the_old_behaviour(engine, monkeypatch):
    monkeypatch.setattr(engine, "OUTPUT_PROFILES", False)
    assert engine._active_output_profile("qwen3.8-27b") == (None, "")


def test_the_write_retry_cap_follows_the_profile_unless_set(engine, monkeypatch):
    monkeypatch.setattr(engine, "_WRITE_CAP_SET", False)
    big = mp.OutputProfile(20000, 8192, 32768, "strict", True, "test")
    assert engine._write_retry_cap(10000, 65000, big) == 20000
    assert engine._write_retry_cap(20000, 65000, big) == 32768
    monkeypatch.setattr(engine, "_WRITE_CAP_SET", True)
    monkeypatch.setattr(engine, "WRITE_OUTPUT_CAP", 16384)
    assert engine._write_retry_cap(10000, 65000, big) == 16384


def _run(engine, monkeypatch, model_name, replies, context=65536, completion=65000):
    from test_output_cap import Script
    model = Script(engine, monkeypatch, replies)
    for flag in ("_INITIAL_CAP_SET", "_MIN_TOKENS_SET", "_THINKING_OFF_SET", "_ADAPTIVE_SET"):
        monkeypatch.setattr(engine, flag, False)
    monkeypatch.setattr(engine, "OUTPUT_PROFILES", True)
    monkeypatch.setattr(engine, "MODEL_NAME", model_name)
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a, **kw: (context, completion))
    monkeypatch.setattr(engine, "_estimate_tokens", lambda *a: 64)
    trace = []
    engine.run_agent([{"role": "user", "content": "check the data"}], max_turns=12, tools_def=[],
                     allowed_tools={"execute_shell"}, trace=trace)
    return model, trace


@pytest.mark.parametrize("name, cap", [("qwen3.8:27b", 20000), ("gemma-4-12B-it-Q4_K_M.gguf", 10000),
                                       ("llama-3.1-70b", 20000), ("qwen3.5-9b", 10000), ("my-finetune", 20000)])
def test_every_ordinary_call_gets_the_models_cap_and_does_not_shrink(engine, monkeypatch, name, cap):
    from test_output_cap import _reply, _tool_reply
    model, trace = _run(engine, monkeypatch, name, [_tool_reply(n, 120) for n in range(5)] + [_reply("done", 50)])
    # Five short replies used to pull the cap down to the floor; it now stays at the model's cap.
    assert model.caps == [cap] * 6
    named = [s for s in trace if s.get("type") == "output_profile"]
    assert len(named) == 1 and named[0]["thinking_cap"] == cap


def test_the_context_window_still_limits_the_request(engine, monkeypatch):
    from test_output_cap import _reply
    model, _trace = _run(engine, monkeypatch, "qwen3.8-27b", [_reply("done", 50)], context=16384)
    assert model.caps[0] < 16384


def test_the_providers_completion_limit_still_wins(engine, monkeypatch):
    from test_output_cap import _reply
    model, _trace = _run(engine, monkeypatch, "qwen3.8-27b", [_reply("done", 50)], completion=8192)
    assert model.caps == [8192]


def test_a_cutoff_is_logged_with_its_repetition(engine, monkeypatch, tmp_path):
    import json

    from test_output_cap import _reply
    log = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_PATH", log)
    monkeypatch.setattr(engine, "CUTOFF_TELEMETRY_TEXT", False)
    looping = "I will check the file again now. " * 400
    _run(engine, monkeypatch, "qwen3.8-27b", [_reply("", finish="length", reasoning=looping), _reply("done")])
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    cut = [r for r in records if r["event"] == "length_cutoff"]
    assert len(cut) == 1
    assert cut[0]["cap"] == 20000 and cut[0]["kind"] == "empty" and "qwen3.8-27b" in cut[0]["profile"]
    assert cut[0]["reasoning"]["cr_tail"] > 6 and cut[0]["reasoning"]["u8_tail"] < 0.6
    assert "tail" not in cut[0]           # no reply text unless asked for
    assert [r["event"] for r in records if r["event"].startswith("length_cutoff")] == [
        "length_cutoff", "length_cutoff_recovered"]


def test_the_reply_text_is_kept_only_when_asked_and_masked(engine, monkeypatch, tmp_path):
    import json
    log = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_PATH", log)
    monkeypatch.setattr(engine, "CUTOFF_TELEMETRY_TEXT", True)
    monkeypatch.setattr(engine, "_redact_secrets", lambda text: text.replace("s3cret", "[masked]"))
    engine.record_cutoff_telemetry("length_cutoff", turn=1, reasoning="x" * 5000 + " token s3cret", content="")
    entry = json.loads(log.read_text(encoding="utf-8"))
    assert len(entry["tail"]) <= engine.CUTOFF_TEXT_CHARS and "[masked]" in entry["tail"]


def test_the_repetition_measures_separate_a_loop_from_ordinary_text():
    from pathlib import Path
    config = Path(loop_check.__file__).with_name("config.txt").read_text(encoding="utf-8")
    prose = " ".join(line.lstrip("# ") for line in config.splitlines() if line.startswith("# "))
    assert loop_check.compression_ratio(prose[-6000:]) < 6 and loop_check.unique_ngram_share(prose[-6000:]) > 0.6
    loop = "Wait, let me re-check the totals. " * 300
    assert loop_check.compression_ratio(loop[-6000:]) > 6 and loop_check.unique_ngram_share(loop[-6000:]) < 0.6
    assert loop_check.unique_ngram_share("too short") == 1.0


def test_the_calls_with_thinking_off_after_a_reasoning_cutoff_get_the_non_thinking_cap(engine, monkeypatch):
    from test_output_cap import _reply, _tool_reply
    monkeypatch.setattr(engine, "CUTOFF_KEEP_REASONING", False, raising=False)   # the discard ladder
    cut = _reply("", finish="length", reasoning="Next I need to build the app " * 50)
    model, _trace = _run(engine, monkeypatch, "qwen3.8-27b",
                         [cut] + [_tool_reply(n, 120) for n in range(5)] + [_reply("done", 50)])
    # The cut-off at the thinking cap, its retry and three sticky calls with thinking off, then thinking again.
    assert model.caps[:6] == [20000, 8192, 8192, 8192, 8192, 20000]
