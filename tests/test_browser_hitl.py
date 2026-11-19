import pytest
from unittest.mock import patch, MagicMock
from agent8088.browser_hitl import create_browser_controller, format_hitl_prompt


def test_format_hitl_prompt_contains_question_and_reason():
    prompt = format_hitl_prompt("Solve Cloudflare", "Bot check detected")
    assert "Solve Cloudflare" in prompt
    assert "Bot check detected" in prompt
    assert "Human in the Loop" in prompt


@pytest.mark.asyncio
async def test_ask_human_empty_input_returns_browser_takeover_completed():
    controller = create_browser_controller()
    action = controller.registry.registry.actions.get("ask_human")
    assert action is not None

    with patch("builtins.input", return_value=""), patch("sys.stdin.isatty", return_value=True):
        result = await action.function(question="Solve captcha", reason="Bot check")
        assert "completed in the browser" in str(result).lower()


@pytest.mark.asyncio
async def test_ask_human_typed_input_calls_callback_and_returns_text():
    mock_cb = MagicMock()
    controller = create_browser_controller(on_human_input=mock_cb)
    action = controller.registry.registry.actions.get("ask_human")
    assert action is not None

    with patch("builtins.input", return_value="123456"), patch("sys.stdin.isatty", return_value=True):
        result = await action.function(question="Enter 2FA", reason="Verification")
        assert "123456" in str(result)
        mock_cb.assert_called_once_with("Enter 2FA", "Verification", "123456")
