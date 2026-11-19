"""/doctor's live checks, `agent8088 --doctor`, `/doctor --fix` repairs and
/api/doctor -- one set of checks behind all of them.

The network (_doctor_http_get), Ollama, MCP, Playwright and Docker are all
mocked; nothing here installs or pulls anything.
"""
import sys

import pytest

from agent8088 import cli


class _Recorder:
    def __init__(self):
        self.lines = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(getattr(a, "plain", a)) for a in args))

    def input(self, prompt=""):
        raise AssertionError("prompted unexpectedly")

    def text(self):
        return "\n".join(self.lines)


@pytest.fixture
def provider(monkeypatch):
    """An active OpenAI-compatible provider with a key, no real endpoint."""
    profile = {"base_url": "http://api.test/v1", "api_key_env": "TEST_API_KEY",
               "api_mode": "openai", "model": "good-model"}
    monkeypatch.setitem(cli.A.PROVIDERS, "testprov", profile)
    monkeypatch.setattr(cli.A, "ACTIVE_PROVIDER", "testprov")
    monkeypatch.setattr(cli.A, "DEFAULT_PROVIDER", "testprov")
    monkeypatch.setattr(cli.A, "MODEL_NAME", "good-model")
    monkeypatch.setattr(cli.A, "_provider_api_key", lambda p: "sk-test" if p else "")
    return profile


def _http(monkeypatch, status=200, payload=None, error=None):
    seen = []

    def fake(url, headers=None, timeout=None):
        seen.append((url, dict(headers or {}), timeout))
        return status, payload, error

    monkeypatch.setattr(cli, "_doctor_http_get", fake)
    return seen


def _by_name(checks, name):
    return next(c for c in checks if c["name"] == name)


def test_provider_ok_and_model_listed(monkeypatch, provider):
    seen = _http(monkeypatch, payload={"data": [{"id": "good-model"}, {"id": "other"}]})
    checks = cli._doctor_provider_checks()
    assert _by_name(checks, "Provider API")["status"] == "ok"
    assert _by_name(checks, "Active model")["status"] == "ok"
    url, headers, timeout = seen[0]
    assert url == "http://api.test/v1/models"
    assert headers["Authorization"] == "Bearer sk-test"


def test_http_get_is_one_short_request_that_never_raises(monkeypatch):
    import httpx
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(timeout)
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", fake_get)
    status, payload, error = cli._doctor_http_get("http://x/models")
    assert status is None and isinstance(error, httpx.ConnectError)
    assert calls == [cli.DOCTOR_HTTP_TIMEOUT_SECONDS]


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_is_a_failure_with_the_env_var(monkeypatch, provider, status):
    _http(monkeypatch, status=status)
    check = _by_name(cli._doctor_provider_checks(), "Provider API")
    assert check["status"] == "fail"
    assert f"HTTP {status}" in check["detail"]
    assert "TEST_API_KEY" in check["fix"] and "--setup" in check["fix"]


def test_a_404_without_v1_suggests_the_v1_url(monkeypatch, provider):
    provider["base_url"] = "http://box:8000"
    _http(monkeypatch, status=404)
    check = _by_name(cli._doctor_provider_checks(), "Provider API")
    assert check["status"] == "fail"
    assert "provider.testprov.base_url=http://box:8000/v1" in check["fix"]


def test_unreachable_endpoint_is_a_failure(monkeypatch, provider):
    _http(monkeypatch, status=None, error=ConnectionRefusedError("connection refused"))
    check = _by_name(cli._doctor_provider_checks(), "Provider API")
    assert check["status"] == "fail"
    assert "Can't reach" in check["detail"]


def test_a_missing_model_on_a_hosted_provider_is_a_warning(monkeypatch, provider):
    _http(monkeypatch, payload={"data": [{"id": "other"}]})
    check = _by_name(cli._doctor_provider_checks(), "Active model")
    assert check["status"] == "warn" and "/models" in check["fix"]


def test_an_unpulled_ollama_model_fails_with_a_pull_repair(monkeypatch):
    monkeypatch.setitem(cli.A.PROVIDERS, "ollama", {"base_url": "http://localhost:11434/v1",
                                                    "model": "qwen-x", "api_key": "ollama"})
    monkeypatch.setattr(cli.A, "ACTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(cli.A, "MODEL_NAME", "qwen-x")
    monkeypatch.setattr(cli.A, "_provider_api_key", lambda p: "ollama")
    seen = _http(monkeypatch, payload={"data": [{"id": "llama3:latest"}]})
    checks = cli._doctor_provider_checks()
    model = _by_name(checks, "Active model")
    assert model["status"] == "fail"
    assert model["fix"].startswith("`ollama pull qwen-x`")
    assert model["repair"] == "ollama_pull:qwen-x"
    assert "Authorization" not in seen[0][1]  # the placeholder key is not sent


def test_ollama_context_smaller_than_planned_is_a_warning(monkeypatch):
    import agent8088.providers as providers
    monkeypatch.setattr(providers, "ollama_served_context", lambda *a, **k: (4096, "model num_ctx"))
    monkeypatch.setattr(cli.A, "_active_model_token_limits", lambda *a, **k: (32768, 8192))
    check = cli._doctor_ollama_context_check("http://localhost:11434/v1", "m")[0]
    assert check["status"] == "warn"
    assert "provider.ollama.context_window=4096" in check["fix"]


def test_file_checks_flag_an_unwritable_home(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.A, "_agent_data_dir", lambda: tmp_path / "home")
    monkeypatch.setattr(cli.A, "ENV_FILE_PATH", tmp_path / "home" / ".env")
    monkeypatch.setattr(cli, "_doctor_writable", lambda path: False)
    monkeypatch.setattr(cli.A, "CONFIG_WARNINGS", ["config.txt: unknown setting 'tempreature'"])
    checks = cli._doctor_files_checks()
    assert _by_name(checks, "Home directory")["status"] == "fail"
    assert "chown" in _by_name(checks, "Home directory")["fix"] or os_is_windows()
    assert _by_name(checks, "Config warning")["detail"].endswith("'tempreature'")


def os_is_windows():
    return sys.platform.startswith("win")


def test_a_missing_config_offers_the_seed_repair(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.A, "CONFIG_PATH", tmp_path / "nope" / "config.txt")
    monkeypatch.setattr(cli.A, "_agent_data_dir", lambda: tmp_path)
    monkeypatch.setattr(cli.A, "ENV_FILE_PATH", tmp_path / ".env")
    check = _by_name(cli._doctor_files_checks(), "User config")
    assert check["repair"] == "seed_config"


def test_mcp_failures_are_listed_per_server(monkeypatch):
    runtime = cli.A.MCP_RUNTIME
    monkeypatch.setattr(runtime, "has_servers", lambda: True)
    monkeypatch.setattr(cli.A, "ensure_mcp_ready", lambda timeout=None: True)
    monkeypatch.setattr(runtime, "statuses", {"good": {"state": "connected", "tools": ["t"]},
                                              "bad": {"state": "error", "error": "spawn failed"}})
    monkeypatch.setattr(cli.A, "mcp_status_summary", lambda: {"pending": False, "connecting": [],
                                                              "connected": ["good"], "tools": 1})
    checks = cli._doctor_mcp_checks()
    assert [c["name"] for c in checks] == ["MCP bad"]
    assert checks[0]["detail"] == "spawn failed" and "mcp.json" in checks[0]["fix"]


def test_missing_chromium_offers_the_playwright_repair(monkeypatch):
    monkeypatch.setattr(cli, "_playwright_chromium_installed", lambda: False)
    check = cli._doctor_browser_checks()[0]
    assert check["status"] == "warn" and check["repair"] == "playwright"
    assert "playwright install chromium" in check["fix"]


def test_searxng_down_is_reported_with_the_fix(monkeypatch):
    monkeypatch.setattr(cli.A, "_docker_available", lambda: False)
    monkeypatch.setitem(cli.A.APP_CONFIG, "search_base_url", "http://127.0.0.1:8888/search?q=")
    monkeypatch.setattr(cli.A, "sandbox_status", lambda: {"requested": "auto"})
    monkeypatch.setattr(cli.A.web_search, "probe_searxng", lambda ctx: False)
    checks = cli._doctor_docker_checks()
    searx = _by_name(checks, "SearXNG")
    assert searx["status"] == "warn" and "Start Docker" in searx["fix"]


def test_memory_mem0_missing_offers_memory_setup(monkeypatch):
    from agent8088 import memory
    monkeypatch.setattr(memory, "memory_status", lambda: {
        "engine": "native", "ok": True,
        "error": "mem0 unavailable, using native memory: No module named 'mem0'", "fix": "x"})
    monkeypatch.setitem(cli.A.APP_CONFIG, "memory_engine", "mem0")
    monkeypatch.setattr(cli, "_doctor_embed_check", lambda: [])
    check = cli._doctor_memory_checks()[0]
    assert check["repair"] == "memory_setup"


# --- report, exit code, rendering ---------------------------------------------

def _fixed_checks(monkeypatch, checks):
    monkeypatch.setattr(cli, "doctor_checks", lambda live=True: checks)


def test_doctor_cli_exits_nonzero_on_a_failure(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(cli, "console", recorder)
    _fixed_checks(monkeypatch, [cli._doctor_check("Provider API", "fail", "API key rejected (HTTP 401)",
                                                  "Check KEY, or run `agent8088 --setup`.")])
    assert cli.run_doctor_cli() == 1
    assert "Check KEY" in recorder.text()


def test_doctor_cli_exits_zero_when_only_warnings(monkeypatch):
    monkeypatch.setattr(cli, "console", _Recorder())
    _fixed_checks(monkeypatch, [cli._doctor_check("Browser (Chromium)", "warn", "missing", "fix")])
    assert cli.run_doctor_cli() == 0


def test_doctor_flag_runs_checks_without_the_repl(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(cli.A, "resolve_auto_search_provider", lambda: None)
    monkeypatch.setattr(cli.A, "verify_sandbox_backend", lambda: None)
    monkeypatch.setattr(cli, "run_doctor_cli", lambda: 7)
    monkeypatch.setattr(cli, "_read_line", lambda: pytest.fail("REPL started"))
    monkeypatch.setattr(sys, "argv", ["agent8088", "--doctor"])
    assert cli.main() == 7


def test_doctor_report_keeps_the_web_fields_and_adds_checks(monkeypatch):
    _fixed_checks(monkeypatch, [
        cli._doctor_check("Model", "info", "testprov:m"),
        cli._doctor_check("Web search", "fail", "ddgs broken - run /doctor --fix"),
        cli._doctor_check("Authentication", "ok", "KEY: set"),
    ])
    report = cli.doctor_report()
    assert report["model"] == "testprov:m"
    assert report["web_search"] == "broken"
    assert report["authentication"] == "KEY: set"
    assert report["ok"] is False
    assert [c["name"] for c in report["checks"]] == ["Model", "Web search", "Authentication"]


def test_api_doctor_returns_the_shared_report(monkeypatch):
    from fastapi.testclient import TestClient

    from agent8088 import web_server
    _fixed_checks(monkeypatch, [cli._doctor_check("Model", "info", "x:y")])
    monkeypatch.setattr(web_server, "_cl", lambda: cli)
    body = TestClient(web_server.app).get("/api/doctor").json()
    assert body["model"] == "x:y" and body["checks"][0]["name"] == "Model"


# --- /doctor --fix -------------------------------------------------------------

def _fix_setup(monkeypatch, tty, answer="y"):
    recorder = _Recorder()
    recorder.input = lambda prompt="": recorder.lines.append(prompt) or answer
    monkeypatch.setattr(cli, "console", recorder)
    monkeypatch.setattr(cli.A.web_search, "_ddgs_installed", lambda: True)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: tty, raising=False)
    monkeypatch.setattr(cli, "WEB_CONFIRM", None)
    pulled = []
    monkeypatch.setattr(cli.A.local_models, "pull_model",
                        lambda name, timeout=None: pulled.append(name) or "success")
    return recorder, pulled


def test_fix_pulls_a_missing_model_after_a_yes(monkeypatch):
    recorder, pulled = _fix_setup(monkeypatch, tty=True, answer="y")
    cli._doctor_fix([cli._doctor_check("Active model", "fail", "x", "f", "ollama_pull:qwen-x")])
    assert pulled == ["qwen-x"]
    assert "Pull qwen-x into Ollama?" in recorder.text()
    assert "Fixed:" in recorder.text()


def test_fix_does_nothing_on_a_no(monkeypatch):
    recorder, pulled = _fix_setup(monkeypatch, tty=True, answer="n")
    cli._doctor_fix([cli._doctor_check("Active model", "fail", "x", "f", "ollama_pull:qwen-x")])
    assert pulled == []


def test_fix_never_installs_without_a_terminal_to_ask(monkeypatch):
    recorder, pulled = _fix_setup(monkeypatch, tty=False)
    ran = []
    monkeypatch.setitem(cli._DOCTOR_REPAIRS, "playwright",
                        ("Install?", lambda arg: ran.append(1) or (True, "x"), "playwright install"))
    cli._doctor_fix([cli._doctor_check("Browser", "warn", "x", "f", "playwright"),
                     cli._doctor_check("Active model", "fail", "x", "f", "ollama_pull:m")])
    assert ran == [] and pulled == []
    assert "skipped (no terminal to ask)" in recorder.text()


def test_fix_from_the_web_needs_an_explicit_yes(monkeypatch):
    recorder, pulled = _fix_setup(monkeypatch, tty=True)
    monkeypatch.setattr(cli, "WEB_CONFIRM", {"command": "/doctor --fix", "yes": False})
    cli._doctor_fix([cli._doctor_check("Active model", "fail", "x", "f", "ollama_pull:m")])
    assert pulled == [] and "/doctor --fix --yes" in recorder.text()
    monkeypatch.setattr(cli, "WEB_CONFIRM", {"command": "/doctor --fix", "yes": True})
    cli._doctor_fix([cli._doctor_check("Active model", "fail", "x", "f", "ollama_pull:m")])
    assert pulled == ["m"]


def test_seed_config_repair_writes_the_packaged_template(monkeypatch, tmp_path):
    target = tmp_path / "sub" / "config.txt"
    monkeypatch.setattr(cli.A, "CONFIG_PATH", target)
    ok, detail = cli._repair_seed_config()
    assert ok and target.exists() and "default_provider" in target.read_text()


def test_playwright_repair_runs_the_install_command(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return cli.subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._repair_playwright()[0] is True
    assert calls == [[sys.executable, "-m", "playwright", "install", "chromium"]]
