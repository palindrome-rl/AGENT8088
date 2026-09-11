import pytest
from unittest.mock import patch, MagicMock
from agent8088 import engine


def test_browser_reuse_session_config_default():
    assert hasattr(engine, "BROWSER_REUSE_SESSION")
    assert hasattr(engine, "BROWSER_HITL")


def test_browser_profile_keeps_alive_when_reuse_enabled():
    with patch("agent8088.engine.BROWSER_REUSE_SESSION", True):
        from agent8088.browser_session import build_session_browser_profile
        profile = build_session_browser_profile(proxy_url="http://127.0.0.1:8080")
        assert profile.keep_alive is True
