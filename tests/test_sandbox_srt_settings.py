"""srt settings: the registry preset by default, package caches the sandbox can write, local ports on macOS, and a
refused host named to the model, logged, and asked about once.

With no sandbox_allowed_domains line the network was closed, so `pip install` failed on a fresh install; with the
line set it still failed on the cache under the home folder, which the sandbox cannot write. A refused host came back
as a bare proxy 403 the model kept retrying."""
import pytest


@pytest.fixture
def data(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(engine, "_secret_scan_roots", lambda workspace: [])
    monkeypatch.setattr(engine, "_session_allowed_hosts", [])
    monkeypatch.setattr(engine, "_hosts_asked", set())
    monkeypatch.setattr(engine, "_audit", lambda event, **fields: audits.append((event, fields)))
    audits = []
    return tmp_path / "data", audits


def test_no_setting_means_the_registry_preset(engine):
    assert engine._sandbox_allowed_domains({}) == list(engine._DEFAULT_SANDBOX_ALLOWED_DOMAINS)
    assert "pypi.org" in engine._sandbox_allowed_domains({"sandbox_allowed_domains": "default,api.example.com"})
    assert engine._sandbox_allowed_domains({"sandbox_allowed_domains": "none"}) == []
    assert engine._sandbox_allowed_domains({"sandbox_allowed_domains": ""}) == []
    assert engine._sandbox_allowed_domains({"sandbox_allowed_domains": "api.example.com"}) == ["api.example.com"]


def test_caches_go_to_a_folder_the_sandbox_may_write(engine, data):
    root, _ = data
    settings = engine._sandbox_settings_data(False, root)
    cache = str((root / "sandbox-cache").resolve())
    assert cache in settings["filesystem"]["allowWrite"]
    env = engine._sandbox_cache_env()
    assert env["PIP_CACHE_DIR"].startswith(cache) and env["npm_config_cache"].startswith(cache)
    assert {"UV_CACHE_DIR", "YARN_CACHE_FOLDER", "CARGO_HOME", "GOMODCACHE", "XDG_CACHE_HOME"} <= set(env)


@pytest.mark.parametrize("platform, binding", [("darwin", True), ("linux", False)])
def test_local_ports_on_macos_only(engine, data, monkeypatch, platform, binding):
    monkeypatch.setattr(engine.sys, "platform", platform)
    assert engine._sandbox_settings_data(False, data[0])["network"]["allowLocalBinding"] is binding


def test_the_command_exports_the_caches_for_every_part(engine, data, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine.sys, "platform", "linux")
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["srt"])
    monkeypatch.setattr(engine, "_exec_process", lambda argv, timeout=25: seen.setdefault("argv", argv) and "ok")
    engine._exec_native_sandbox("pip install x && npm install", 10, cwd=data[0])
    command = seen["argv"][-1]
    assert " && export TMPDIR=" in command and "PIP_CACHE_DIR=" in command
    assert command.endswith("pip install x && npm install")
    assert command.index("export TMPDIR=") < command.index("pip install x")


@pytest.mark.parametrize("host, allowed", [
    ("pypi.org", True), ("api.github.com", True), ("github.com", True), ("evil-github.com", False),
    ("raw.githubusercontent.com", True), ("example.com", False),
])
def test_host_matching(engine, host, allowed):
    assert engine._host_is_allowed(host, ["pypi.org", "github.com", "*.github.com", "*.githubusercontent.com"]) is allowed


PIP_REFUSED = ("WARNING: Retrying (Retry(total=4)) after connection broken by 'ProxyError('Cannot connect to proxy.', "
               "OSError('Tunnel connection failed: 403 Forbidden'))': /simple/requests/")


def test_a_refused_host_is_named_and_logged(engine, data, monkeypatch):
    _, audits = data
    monkeypatch.setattr(engine, "SANDBOX_ALLOWED_DOMAINS", ["pypi.org"])
    result = engine._refused_host_result(PIP_REFUSED, "pip install --index-url https://pkgs.example.com/simple requests",
                                         interactive=False)
    assert "could not reach pkgs.example.com" in result and "will not help" in result
    assert audits and audits[0][0] == "sandbox_host_refused" and audits[0][1]["hosts"] == ["pkgs.example.com"]


def test_an_allowed_host_or_a_server_403_adds_nothing(engine, data, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_ALLOWED_DOMAINS", ["pypi.org"])
    assert engine._refused_host_result(PIP_REFUSED, "pip install requests", interactive=False) == PIP_REFUSED
    server = "curl: (22) The requested URL returned error: 403"
    assert engine._refused_host_result(server, "curl -f https://example.com/x", interactive=False) == server


def test_the_user_is_asked_once_per_host_and_an_allowed_host_joins_the_session(engine, data, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_ALLOWED_DOMAINS", ["pypi.org"])
    monkeypatch.setattr(engine, "SANDBOX_ASK_HOSTS", True)
    command = "curl https://api.example.com/v1"
    refused = "curl: (56) CONNECT tunnel failed, response 403"
    first = engine._refused_host_result(refused, command, interactive=True)
    assert first.startswith("ESCALATION_REQUEST\x1f") and "\x1fsandbox_host:api.example.com\x1fapi.example.com\x1f" in first
    again = engine._refused_host_result(refused, command, interactive=True)
    assert not again.startswith("ESCALATION_REQUEST") and "could not reach api.example.com" in again
    engine.grant_escalation("sandbox_host:api.example.com", "api.example.com")
    assert "api.example.com" in engine._effective_allowed_domains()
    assert "api.example.com" in engine._sandbox_settings_data(False, data[0])["network"]["allowedDomains"]
    assert engine._refused_host_result(refused, command, interactive=True) == refused


def test_no_question_when_asking_is_off_or_nobody_is_there(engine, data, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_ALLOWED_DOMAINS", [])
    refused = "npm ERR! request to https://registry.example.org/x failed, reason: tunneling socket could not be established, statusCode=403"
    monkeypatch.setattr(engine, "SANDBOX_ASK_HOSTS", False)
    assert not engine._refused_host_result(refused, "npm install", interactive=True).startswith("ESCALATION")
    monkeypatch.setattr(engine, "SANDBOX_ASK_HOSTS", True)
    assert not engine._refused_host_result(refused, "npm install", interactive=False).startswith("ESCALATION")


def test_a_bad_host_name_is_never_allowed(engine, data):
    engine.grant_escalation("sandbox_host:ok.example.com,*,evil host", "*,ok.example.com, ,evil host")
    assert engine._session_allowed_hosts == ["ok.example.com"]


def test_a_session_approval_covers_only_the_hosts_it_named(engine, data):
    # A front end that remembers "allow for the session" by change type re-grants that
    # type later with whatever the new request lists; only the named host may come in.
    engine.grant_escalation("sandbox_host:api.example.com", "api.example.com")
    engine.grant_escalation("sandbox_host:api.example.com", "evil.example.net,api.example.com")
    engine.grant_escalation("sandbox_host", "evil.example.net")
    assert engine._session_allowed_hosts == ["api.example.com"]


def test_two_hosts_are_two_different_questions(engine, data, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_ALLOWED_DOMAINS", [])
    monkeypatch.setattr(engine, "SANDBOX_ASK_HOSTS", True)
    refused = "curl: (56) CONNECT tunnel failed, response 403"
    first = engine._refused_host_result(refused, "curl https://a.example.com", interactive=True)
    second = engine._refused_host_result(refused, "curl https://b.example.com", interactive=True)
    assert first.split("\x1f")[2] == "sandbox_host:a.example.com"
    assert second.split("\x1f")[2] == "sandbox_host:b.example.com"


@pytest.mark.parametrize("relative", [".git-credentials", ".pypirc", ".cargo/credentials.toml",
                                      ".config/git/credentials", ".gem/credentials"])
def test_home_credential_files_are_hidden(engine, data, monkeypatch, tmp_path, relative):
    home = tmp_path / "home"
    monkeypatch.setattr(engine.Path, "home", classmethod(lambda cls: home))
    settings = engine._sandbox_settings_data(False, data[0])
    hidden = str((home / relative).resolve())
    assert hidden in settings["filesystem"]["denyRead"] and hidden in settings["filesystem"]["denyWrite"]


def test_the_node_the_installer_downloaded_is_found_without_path(engine, data, monkeypatch):
    # A machine with no system Node gets one under the data folder, on PATH only while
    # the installer runs; a later terminal must still find it.
    root, _ = data
    monkeypatch.setattr(engine.sys, "platform", "linux")
    monkeypatch.delenv("AGENT8088_SRT", raising=False)
    monkeypatch.setattr(engine, "_which_executable", lambda name: None)
    cli = root / "runtime" / "node_modules" / "@anthropic-ai" / "sandbox-runtime" / "dist" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("", encoding="utf-8")
    assert engine._native_sandbox_argv() is None
    node = root / "node" / "bin" / "node"
    node.parent.mkdir(parents=True)
    node.write_text("", encoding="utf-8")
    node.chmod(0o755)
    assert engine._native_sandbox_argv() == [str(node), str(cli)]
