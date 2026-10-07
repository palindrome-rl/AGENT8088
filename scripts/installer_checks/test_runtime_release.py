"""Offline acceptance checks for the runtime shipped on the public branch."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "absent.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT8088_SANDBOX", "local")
    from agent8088 import capabilities, engine
    capabilities.reset()
    engine = importlib.reload(engine)
    yield engine
    capabilities.reset()


def test_length_recovery_respects_available_context_without_a_maintainer_config(runtime, monkeypatch):
    calls = []

    def completion(messages, tools, max_tokens=None, **kwargs):
        calls.append(max_tokens)
        first = len(calls) == 1
        message = SimpleNamespace(content="<think>unfinished" if first else "Recovered answer")
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="length" if first else "stop")])

    monkeypatch.setattr(runtime, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(runtime, "_active_model_token_limits", lambda *args, **kwargs: (32768, 32768))
    monkeypatch.setattr(runtime.memory, "enabled", lambda: False)
    answer = runtime.run_agent([{"role": "user", "content": "hello"}], max_turns=5)
    assert answer == "Recovered answer" and len(calls) == 2
    assert 0 < calls[1] <= calls[0] <= 32768


def test_capability_registry_preserves_multiple_limited_features(runtime):
    from agent8088 import capabilities as c
    c.report(c.SEARCH, active="ddgs", preferred="searxng", state=c.DEGRADED,
             reason="offline", fix="/search setup")
    c.report(c.CONTEXT, active="assumed", state=c.DEGRADED, reason="unknown limit")
    rows = {row["name"]: row for row in c.rows()}
    assert rows["search"]["active"] == "ddgs"
    assert rows["context"]["state"] == "degraded"
    assert "search" in c.banner_line()


def test_install_ledger_reaches_live_capability_reporting(runtime, tmp_path):
    from agent8088 import capabilities as c, install_state
    path = tmp_path / "install-state.json"
    path.write_text(json.dumps({"version": 1, "installed_at": "2026-10-07T00:00:00Z",
        "skipped": [{"stage": "First-run setup", "reason": "not completed", "fix": "agent8088 --setup"}]}),
        encoding="utf-8")
    assert install_state.report(path)
    entry = c.get(c.INSTALL)
    assert entry.state == c.DEGRADED and "at install time" in entry.reason
    assert "agent8088 --setup" in entry.fix


def test_web_status_reports_search_even_when_other_capabilities_are_limited(runtime, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent8088 import capabilities as c, cli, web_server
    monkeypatch.setattr(web_server, "_eng", lambda: runtime)
    monkeypatch.setattr(web_server, "_cl", lambda: cli)
    c.report(c.SEARCH, active="ddgs", preferred="searxng", state=c.DEGRADED,
             reason="offline", fix="/search setup")
    c.report(c.CONTEXT, active="assumed", state=c.DEGRADED, reason="unknown limit")
    rows = TestClient(web_server.app).get("/api/status").json()["capabilities"]
    search = next(row for row in rows if row["name"] == "search")
    assert search["active"] == "ddgs" and search["fix"] == "/search setup"


def test_display_keeps_evidence_but_removes_internal_markers(runtime):
    raw = '<<<EXTERNAL_UNTRUSTED_CONTENT source="shell">>>\nfile.txt\n<<<END_UNTRUSTED_CONTENT>>>'
    result = runtime.display_tool_result("execute_shell", raw)
    assert "file.txt" in result and "UNTRUSTED_CONTENT" not in result


def test_public_cli_version_and_update_channel(runtime):
    from agent8088 import __version__, cli
    assert __version__ == "1.2.0"
    assert cli.UPDATE_BRANCH == "AGENT8088-v1.2"


def test_doctor_shortens_both_path_styles_without_shortening_another_user(runtime, monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: Path("/Users/al")))
    assert cli._tilde("/Users/al/x and /Users/alice/y and /Users/al") == "~/x and /Users/alice/y and ~"


@pytest.mark.parametrize("query,wait", [("remember that I use tabs", True), ("hello there", False)])
def test_explicit_memory_saves_wait_but_normal_turns_do_not(runtime, monkeypatch, query, wait):
    from agent8088 import cli
    joined = []
    class Capture:
        def is_alive(self):
            return True
        def join(self, timeout):
            joined.append(timeout)
    monkeypatch.setattr(cli.A, "memory_capture_thread", Capture())
    monkeypatch.setattr(cli, "_pending_captures", [])
    monkeypatch.setattr(cli, "_report_pending_capture", lambda: None)
    cli._await_memory_capture([], query)
    assert bool(joined) is wait


def test_public_wiki_sync_preserves_unrelated_navigation():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("public_wiki", root / "scripts/sync_wiki.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    other = "## Community\n- [Support](Support)\n"
    first = module.update_sidebar(other)
    second = module.update_sidebar(first)
    assert other.strip() in second
    assert second.count(module.SIDEBAR_START) == 1
    assert module.REPO == "palindrome-rl/AGENT8088" and module.SOURCE_BRANCH == "AGENT8088-v1.2"
