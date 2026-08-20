"""Shared fixtures: load the agent8088 engine as a module."""
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_engine():
    os.environ["AGENT8088_CONFIG"] = str(ROOT / "_no_such_config.txt")
    os.environ["AGENT8088_SANDBOX"] = "local"
    sys.path.insert(0, str(ROOT / "src"))
    from agent8088 import engine as mod
    return importlib.reload(mod)


@pytest.fixture
def engine():
    """Fresh engine module per test (module globals are mutable in tests)."""
    return _load_engine()
