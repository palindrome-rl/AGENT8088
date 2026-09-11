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


@pytest.mark.asyncio
async def test_browser_agent_receives_browser_session_when_reuse_enabled():
    passed_kwargs = {}

    class MockAgent:
        def __init__(self, **kwargs):
            passed_kwargs.update(kwargs)

        async def run(self, max_steps=None):
            m = MagicMock()
            m.final_result.return_value = "done"
            m.is_done.return_value = True
            return m

        async def close(self):
            pass

    with patch("agent8088.engine.BROWSER_REUSE_SESSION", True), \
         patch("browser_use.Agent", MockAgent), \
         patch("agent8088.browser_llm.build_browser_chat_model", return_value=MagicMock()):
        res = await engine._run_browser_agent("https://example.com", "test task")
        assert "browser_session" in passed_kwargs
        assert passed_kwargs["browser_session"] is not None
        assert passed_kwargs["initial_actions"] == [{"navigate": {"url": "https://example.com", "new_tab": False}}]
