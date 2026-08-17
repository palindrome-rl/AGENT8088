"""Tests for the /search command and the wizard's web search step.

No docker, no network: searxng_provision is patched throughout.
"""
import sys

import pytest

from agent8088 import cli


@pytest.fixture(autouse=True)
def _no_docker_calls(monkeypatch):
    """Nothing in this module may shell out to docker or poll a real instance."""
    monkeypatch.setattr(cli.searxng_provision.shutil, "which", lambda n: None)
    monkeypatch.setattr(
        cli.searxng_provision.subprocess, "run",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no subprocess in tests")))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def test_search_command_is_registered():
    assert "search" in cli.COMMANDS
    assert cli.COMMANDS["search"] is cli.cmd_search


def test_search_is_tab_completable():
    assert "search" in cli._COMPLETABLE_COMMANDS


def test_repl_startup_does_not_print_search_backend(capsys, monkeypatch):
    """Backend selection is available through /search, not startup clutter."""
    monkeypatch.setattr(sys, "argv", ["agent8088"])
    monkeypatch.setattr(cli.A, "resolve_auto_search_provider", lambda: "ddgs")
    monkeypatch.setattr(cli, "_install_completion", lambda: None)
    monkeypatch.setattr(cli, "banner", lambda: None)
    monkeypatch.setattr(cli, "warn_about_unknown_theme", lambda: None)
    monkeypatch.setattr(cli, "_read_line", lambda: "exit")

    cli.main()

    assert "web search:" not in capsys.readouterr().out.lower()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def test_search_status_lists_every_backend_including_unconfigured(capsys):
    """Optional backends must be DISCOVERABLE even with no key — otherwise a
    user never learns Tavily/Exa are available to them."""
    cli.cmd_search("status")
    out = capsys.readouterr().out.lower()
    for name in ("searxng", "tavily", "exa", "ddgs"):
        assert name in out


def test_search_status_names_the_env_var_that_enables_a_backend(capsys):
    cli.cmd_search("status")
    out = capsys.readouterr().out
    assert "TAVILY_API_KEY" in out and "EXA_API_KEY" in out


def test_search_status_shows_the_active_chain(capsys, monkeypatch):
    monkeypatch.setattr(cli.A, "_search_chain_summary", lambda: "searxng -> ddgs")
    cli.cmd_search("")
    assert "searxng -> ddgs" in capsys.readouterr().out


def test_search_status_never_prints_a_key_value(capsys, monkeypatch):
    """Names yes, values never."""
    monkeypatch.setattr(cli.A, "_search_context",
                        lambda: cli.A.web_search.SearchContext(
                            get_secret=lambda n: "tvly-SUPERSECRET"))
    cli.cmd_search("status")
    assert "SUPERSECRET" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# use
# ---------------------------------------------------------------------------
def test_search_use_rejects_unknown_provider(capsys, monkeypatch):
    written = {}
    monkeypatch.setattr(cli.A, "update_simple_config",
                        lambda path, values: written.update(values))
    cli.cmd_search("use nope")
    assert "nope" in capsys.readouterr().out
    assert written == {}  # nothing persisted


def test_search_use_persists_a_known_provider(capsys, monkeypatch):
    written = {}
    monkeypatch.setattr(cli.A, "update_simple_config",
                        lambda path, values: written.update(values))
    cli.cmd_search("use tavily")
    assert written.get("web_search_provider") == "tavily"


def test_search_use_warns_but_still_persists_an_unavailable_provider(capsys, monkeypatch):
    """Pinning tavily before pasting the key must not be a dead end."""
    written = {}
    monkeypatch.setattr(cli.A, "update_simple_config",
                        lambda path, values: written.update(values))
    cli.cmd_search("use exa")
    out = capsys.readouterr().out.lower()
    assert written.get("web_search_provider") == "exa"
    assert "not currently available" in out or "not available" in out


# ---------------------------------------------------------------------------
# setup / stop
# ---------------------------------------------------------------------------
def test_search_setup_without_docker_points_at_the_bundled_fallback(capsys):
    cli.cmd_search("setup")
    out = capsys.readouterr().out.lower()
    assert "ddgs" in out
    # It ships with the agent — setup must not imply an install step.
    assert "pip install ddgs" not in out


def test_search_setup_with_docker_provisions_and_persists(capsys, monkeypatch):
    written = {}
    monkeypatch.setattr(cli.A, "_docker_available", lambda: True)
    monkeypatch.setattr(cli.searxng_provision, "start",
                        lambda home: {"ok": True, "detail": "started",
                                      "base_url": "http://127.0.0.1:8888/search?q="})
    monkeypatch.setattr(cli.searxng_provision, "wait_ready",
                        lambda **kw: {"ok": True, "detail": "JSON API responding"})
    monkeypatch.setattr(cli.A, "update_simple_config",
                        lambda path, values: written.update(values))
    monkeypatch.setattr(cli.A, "resolve_auto_search_provider", lambda: "searxng")
    cli.cmd_search("setup")
    assert written.get("search_base_url") == "http://127.0.0.1:8888/search?q="
    assert written.get("web_search_provider") == "auto"


def test_search_setup_does_not_persist_when_readiness_fails(capsys, monkeypatch):
    """A container that never answers must not be recorded as the backend."""
    written = {}
    monkeypatch.setattr(cli.A, "_docker_available", lambda: True)
    monkeypatch.setattr(cli.searxng_provision, "start",
                        lambda home: {"ok": True, "detail": "started",
                                      "base_url": "http://127.0.0.1:8888/search?q="})
    monkeypatch.setattr(cli.searxng_provision, "wait_ready",
                        lambda **kw: {"ok": False, "detail": "did not become ready"})
    monkeypatch.setattr(cli.A, "update_simple_config",
                        lambda path, values: written.update(values))
    cli.cmd_search("setup")
    assert written == {}
    assert "did not become ready" in capsys.readouterr().out


def test_search_stop_reports_result(capsys, monkeypatch):
    monkeypatch.setattr(cli.searxng_provision, "stop",
                        lambda: {"ok": True, "detail": "removed"})
    cli.cmd_search("stop")
    assert "removed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
def test_search_doctor_reports_container_and_ssrf_coverage(capsys, monkeypatch):
    monkeypatch.setattr(cli.searxng_provision, "status",
                        lambda: {"running": False, "detail": "container does not exist"})
    cli.cmd_search("doctor")
    out = capsys.readouterr().out.lower()
    assert "container does not exist" in out
    assert "ssrf" in out


def test_search_doctor_flags_a_host_missing_from_ssrf_allowlist(capsys, monkeypatch):
    monkeypatch.setattr(cli.searxng_provision, "status",
                        lambda: {"running": True, "detail": "running"})
    monkeypatch.setitem(cli.A.APP_CONFIG, "search_base_url",
                        "http://searx.internal:8888/search?q=")
    monkeypatch.setattr(cli.A, "SEARCH_BASE_URL_CONFIGURED", True)
    monkeypatch.setattr(cli.A, "SSRF_ALLOW_HOSTS", {"127.0.0.1", "localhost"})
    cli.cmd_search("doctor")
    out = capsys.readouterr().out
    assert "searx.internal" in out and "ssrf_allow_hosts" in out


def test_search_doctor_accepts_host_and_port_ssrf_allowlist_entry(capsys, monkeypatch):
    monkeypatch.setattr(cli.searxng_provision, "status",
                        lambda: {"running": True, "detail": "running"})
    monkeypatch.setitem(cli.A.APP_CONFIG, "search_base_url",
                        "http://searx.internal:8888/search?q=")
    monkeypatch.setattr(cli.A, "SEARCH_BASE_URL_CONFIGURED", True)
    monkeypatch.setattr(cli.A, "SSRF_ALLOW_HOSTS", {"searx.internal:8888"})

    cli.cmd_search("doctor")

    assert "does not include searx.internal" not in capsys.readouterr().out.lower()


# ---------------------------------------------------------------------------
# Wizard options
# ---------------------------------------------------------------------------
def test_wizard_offers_searxng_first_when_docker_present(monkeypatch):
    monkeypatch.setattr(cli.A, "_docker_available", lambda: True)
    options = cli._search_setup_options()
    assert options[0].startswith("SearXNG")


def test_wizard_offers_ddgs_first_without_docker(monkeypatch):
    monkeypatch.setattr(cli.A, "_docker_available", lambda: False)
    options = cli._search_setup_options()
    assert "ddgs" in options[0].lower()
    # It ships with the agent — the wizard must not imply an install step.
    assert "install" not in options[0].lower()


def test_wizard_always_offers_both_optional_key_backends(monkeypatch):
    monkeypatch.setattr(cli.A, "_docker_available", lambda: True)
    joined = " ".join(cli._search_setup_options()).lower()
    assert "tavily" in joined and "exa" in joined


def test_wizard_offers_a_way_to_disable_search(monkeypatch):
    monkeypatch.setattr(cli.A, "_docker_available", lambda: True)
    assert any("none" in o.lower() for o in cli._search_setup_options())
