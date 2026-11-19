from unittest.mock import patch
from agent8088 import cli


def test_browser_close_command_triggers_cleanup():
    with patch("agent8088.browser_session.cleanup_browser_session") as mock_cleanup, \
         patch("agent8088.browser_session.is_browser_session_active", return_value=True):
        res = cli.handle_browser_command(["close"])
        assert "closed" in res.lower()
        mock_cleanup.assert_called_once()


def test_browser_status_command_reports_idle():
    with patch("agent8088.browser_session.is_browser_session_active", return_value=False):
        res = cli.handle_browser_command(["status"])
        assert "idle" in res.lower()
