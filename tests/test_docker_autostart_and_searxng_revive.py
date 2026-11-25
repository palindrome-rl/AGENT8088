"""Docker Desktop is launched when needed, and a stopped managed SearXNG is
brought back (feature suggestion: self-launch Docker; issue sheet row 33)."""
import sys

import pytest

from agent8088 import searxng_provision


@pytest.fixture
def desktop(engine, monkeypatch, tmp_path):
    app = tmp_path / "Docker Desktop.exe"
    app.write_text("")
    launched, state = [], {"up": False, "after": 2}
    monkeypatch.setitem(engine._DOCKER_DESKTOP_APPS, sys.platform, [app])
    monkeypatch.setattr(engine, "_which_executable", lambda name: "docker")
    monkeypatch.setattr(engine.subprocess, "Popen", lambda argv, **k: launched.append(argv))

    def available():
        if launched:
            state["after"] -= 1
        return state["up"] or (launched and state["after"] <= 0)
    monkeypatch.setattr(engine, "_docker_available", available)
    monkeypatch.setenv("AGENT8088_DOCKER_AUTOSTART", "1")
    return launched, state


def test_a_running_docker_is_left_alone(engine, desktop):
    launched, state = desktop
    state["up"] = True
    assert engine._start_docker_desktop(sleep=lambda s: None) is True
    assert launched == []


def test_a_stopped_docker_desktop_is_launched_and_awaited(engine, desktop):
    launched, _ = desktop
    assert engine._start_docker_desktop(sleep=lambda s: None) is True
    assert len(launched) == 1


def test_autostart_can_be_turned_off(engine, desktop, monkeypatch):
    launched, _ = desktop
    monkeypatch.delenv("AGENT8088_DOCKER_AUTOSTART")
    monkeypatch.setitem(engine.APP_CONFIG, "docker_autostart", "0")
    assert engine._start_docker_desktop(sleep=lambda s: None) is False
    assert launched == []


@pytest.fixture
def searxng(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_start_docker_desktop", lambda **kwargs: True)
    monkeypatch.setattr(searxng_provision, "status", lambda: {"running": False})
    monkeypatch.setattr(searxng_provision, "resume", lambda: calls.append("resume") or True)
    monkeypatch.setitem(engine.APP_CONFIG, "web_search_provider", "ddgs")
    monkeypatch.setitem(engine._SEARCH_AUTO, "pin", "ddgs")
    monkeypatch.setattr(engine, "_set_auto_pin", lambda name, why: calls.append(name))
    monkeypatch.setattr(engine, "_report_search_state", lambda *a, **k: None)
    return calls


def _url(engine, monkeypatch, url):
    monkeypatch.setattr(engine, "_search_config", lambda: {"search_base_url": url})


def test_a_stopped_managed_container_is_resumed_and_used(engine, monkeypatch, searxng):
    _url(engine, monkeypatch, "http://127.0.0.1:8888/search?q=")
    assert engine._revive_managed_searxng(wait=lambda port: {"ok": port == 8888}) is True
    assert searxng == ["resume", "searxng"]


def test_a_remote_searxng_is_never_touched(engine, monkeypatch, searxng):
    _url(engine, monkeypatch, "https://search.example.org/search?q=")
    assert engine._revive_managed_searxng(wait=lambda port: {"ok": True}) is False
    assert searxng == []


def test_a_removed_container_is_not_recreated(monkeypatch):
    monkeypatch.setattr(searxng_provision, "_docker", lambda: "docker")
    monkeypatch.setattr(searxng_provision, "status",
                        lambda: {"running": False, "detail": "container does not exist"})
    monkeypatch.setattr(searxng_provision, "_run", lambda *a, **k: pytest.fail("ran docker"))
    assert searxng_provision.resume() is False
