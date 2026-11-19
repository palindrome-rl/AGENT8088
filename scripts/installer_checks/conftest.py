"""Isolated runtime for public regression cases; never use a real installation."""
import importlib

import pytest


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "absent.txt"))
    monkeypatch.setenv("AGENT8088_DOCKER_AUTOSTART", "0")
    from agent8088 import capabilities, engine as runtime
    capabilities.reset()
    runtime = importlib.reload(runtime)
    runtime.FILE_BACKUPS = False
    yield runtime
    capabilities.reset()
