from unittest.mock import MagicMock, patch
from agent8088.browser_memory import (
    extract_domain,
    recall_browser_domain_preferences,
    record_browser_preference,
)


def test_extract_domain():
    assert extract_domain("https://news.ycombinator.com/item?id=123") == "news.ycombinator.com"
    assert extract_domain("http://github.com/login") == "github.com"
    assert extract_domain("not-a-url") == ""


def test_recall_browser_domain_preferences():
    mock_memory = MagicMock()
    mock_memory.recall.return_value = ["User prefers dark mode on github.com"]
    with patch("agent8088.browser_memory.get_active_memory", return_value=mock_memory):
        prefs = recall_browser_domain_preferences("github.com")
        assert len(prefs) == 1
        assert "dark mode" in prefs[0]


def test_record_browser_preference():
    mock_memory = MagicMock()
    with patch("agent8088.browser_memory.get_active_memory", return_value=mock_memory):
        record_browser_preference("amazon.com", "Always pick free standard shipping")
        mock_memory.store.assert_called_once()
        args, _ = mock_memory.store.call_args
        assert "amazon.com" in args[0]
        assert "free standard shipping" in args[0]
