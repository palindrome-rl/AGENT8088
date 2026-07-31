from unittest.mock import MagicMock, patch
from agent8088.gateway.session import SessionStore
from agent8088.gateway.agent_bridge import run_turn


def test_run_turn_loads_session_calls_engine_saves(tmp_path):
    store = SessionStore(base_dir=str(tmp_path))
    key = "agent:main:slack:private:U1"

    with patch("agent8088.gateway.agent_bridge.A") as mock_A:
        mock_A.APP_CONFIG = {"max_turns": "5", "temperature": "0.2"}
        mock_A.BASE_SYSTEM_PROMPT = "You are a test agent."
        mock_A.TOOL_SPECS = {"execute_shell": {"mode": "shell"}}
        mock_A.build_tools_def.return_value = []
        mock_A.run_agent.return_value = "Hello from agent"
        mock_A.strip_tool_json.side_effect = lambda x: x

        answer = run_turn(key, "hi there", session_store=store)

    assert answer == "Hello from agent"
    mock_A.run_agent.assert_called_once()
    saved = store.load(key)
    assert len(saved) == 2
    assert saved[0] == {"role": "user", "content": "hi there"}
    assert saved[1] == {"role": "assistant", "content": "Hello from agent"}


def test_run_turn_appends_to_existing_session(tmp_path):
    store = SessionStore(base_dir=str(tmp_path))
    key = "agent:main:slack:private:U1"
    store.save(key, [{"role": "user", "content": "previous"}])

    with patch("agent8088.gateway.agent_bridge.A") as mock_A:
        mock_A.APP_CONFIG = {}
        mock_A.BASE_SYSTEM_PROMPT = "sys"
        mock_A.TOOL_SPECS = {}
        mock_A.build_tools_def.return_value = []
        mock_A.run_agent.return_value = "reply"
        mock_A.strip_tool_json.side_effect = lambda x: x

        run_turn(key, "new question", session_store=store)

    saved = store.load(key)
    assert len(saved) == 3
    assert saved[0]["content"] == "previous"
    assert saved[1]["content"] == "new question"
    assert saved[2]["content"] == "reply"


def test_run_turn_no_streaming(tmp_path):
    store = SessionStore(base_dir=str(tmp_path))
    key = "test_no_stream"

    with patch("agent8088.gateway.agent_bridge.A") as mock_A:
        mock_A.APP_CONFIG = {}
        mock_A.BASE_SYSTEM_PROMPT = "sys"
        mock_A.TOOL_SPECS = {}
        mock_A.build_tools_def.return_value = []
        mock_A.run_agent.return_value = "Done!"

        answer = run_turn(key, "do something", session_store=store)

    assert answer == "Done!"
    # Verify on_token was NOT passed to run_agent (no streaming in gateway)
    _, kwargs = mock_A.run_agent.call_args
    assert "on_token" not in kwargs