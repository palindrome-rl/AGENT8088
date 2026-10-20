"""Offline acceptance checks for memory, evidence, scanning and limit defaults."""
import builtins
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "absent.txt"))
    from agent8088 import engine
    return importlib.reload(engine)


def test_new_defaults_and_public_channel(runtime):
    from agent8088 import cli
    assert runtime.DEFAULT_MAX_TURNS == 400
    assert runtime.DYNAMIC_TURNS_CEILING_MULTIPLIER == 3
    assert runtime._turn_policy(runtime.DEFAULT_MAX_TURNS).ceiling == 1200
    assert runtime.PLAN_MODE_TIMEOUT_SECONDS == 600
    assert runtime.MAX_TOOL_TIMEOUT_SECONDS == 1200
    assert cli.UPDATE_BRANCH == "AGENT8088-v1.3"
    template = Path(runtime.__file__).with_name("config.txt").read_text(encoding="utf-8")
    assert "\nmemory_engine=native\n" in template
    assert "\nmax_turns=400\n" in template
    assert "# plan_mode_timeout_seconds=600" in template
    assert "/limits" in dict(cli._ESSENTIAL_COMMANDS)


@pytest.mark.parametrize("missing", [True, False])
def test_missing_mem0_is_quiet_but_broken_import_is_not(monkeypatch, tmp_path, missing):
    from agent8088.memory.mem0_store import Mem0MemoryStore
    original = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mem0":
            if missing:
                raise ModuleNotFoundError("optional package absent", name="mem0")
            raise RuntimeError("synthetic installed-package import failure")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    store = Mem0MemoryStore(config={"path": str(tmp_path / "memory")})
    assert not store.available()
    assert store.quiet is missing


def test_smoke_evidence_expires_after_an_edit():
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal="build page", message_count=1)
    state.data.update(mutation_count=1, verification="pending")
    state.record_smoke("index.html", "index.html: smoke test passed", failed=False, passed=True)
    assert state.assurance() == "exercised"
    operation = state.before_tool("write_file")
    state.after_tool(operation, "wrote page", failed=False, blocked=False, mutated=True, path="index.html")
    assert state.assurance() == "unchecked"


def test_windows_chains_and_fake_summaries(runtime):
    from agent8088.trajectory import _is_check_run
    assert _is_check_run("copy a b & python -m unittest")
    assert not _is_check_run('echo "label; tests"')
    assert runtime._runs_a_test_runner("python -m pytest 2>&1 | tail -20")
    assert not runtime._runs_a_test_runner('pytest; echo "12 passed"')


def test_plain_pytest_functions_choose_pytest(tmp_path):
    from agent8088 import completion
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_app():\n    assert False\n", encoding="utf-8")
    assert completion.tests_near([str(tmp_path / "app.py")])[1] == "python -m pytest -q"


@pytest.mark.parametrize("metadata", ['{"name":"test"}', '{"scripts":{"test":', '{"scripts":{"test":""}}'])
def test_npm_requires_a_real_test_script(tmp_path, metadata):
    from agent8088 import completion
    (tmp_path / "package.json").write_text(metadata, encoding="utf-8")
    assert completion.tests_near([str(tmp_path / "app.js")]) is None


@pytest.mark.parametrize("chunks", [
    ("QJXNZKPVTR", "WH7LMBCDYFS"),
    ("qjxnzkpvtr", "wh7lmbcdyfs"),
    ("QjxNZKPVTR", "WH7LmbCDYFS"),
])
def test_synthetic_unstructured_blobs_are_not_code_identifiers(chunks):
    from agent8088 import secret_scan
    assert secret_scan.find_secrets("".join(chunks))


@pytest.mark.parametrize("text", ["raiseSpA2Speed(target, 1)", "OLLAMA_MODEL=tev1:0.8b"])
def test_identifiers_and_model_sizes_are_not_credentials(text):
    from agent8088 import secret_scan
    assert not secret_scan.find_secrets(text)


def test_large_diagnostics_cannot_fill_context(runtime):
    text = "log.txt:12: " + "x" * 2_400_000 + "\nFAILED (failures=1)\n"
    shown = runtime._tool_result_for_model("execute_shell", text)
    assert shown.startswith("[verdict: FAILED]")
    assert "read_content" in shown
    assert len(shown) < runtime.TOOL_RESULT_MAX_CHARS + 4500
