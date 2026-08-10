"""Tool selection: don't pile more tools onto a search that already worked.

The browser gate has existed for a while. Shell and MCP were the open doors —
the model could answer a search by shelling out to curl, or by calling an MCP
"web search" tool, and the whole point of the gate was lost.

Deliberately narrow: only fetch-shaped calls are gated. After a search the
agent may still legitimately install a package or read a file, and blocking
that would be a worse bug than the redundant fetch.
"""
import pytest


def _user(text):
    return [{"role": "user", "content": text}]


# --- explicit user requests bypass every gate ---------------------------

def test_named_tool_counts_as_a_request(engine):
    assert engine._user_requested_tool(_user("use execute_shell for this"), "execute_shell")


def test_plain_language_request_counts(engine):
    assert engine._user_requested_tool(_user("run `ls -la` in the repo"), "execute_shell")


def test_unrelated_message_is_not_a_request(engine):
    assert not engine._user_requested_tool(_user("who is the UK PM?"), "execute_shell")


def test_assistant_text_does_not_count_as_a_request(engine):
    """Only the user can ask for a tool — the model must not authorise itself."""
    messages = [{"role": "assistant", "content": "I will run execute_shell now"}]

    assert not engine._user_requested_tool(messages, "execute_shell")


# --- the gate itself ------------------------------------------------------

def _drive(engine, monkeypatch, scripted, responses, user_text="latest python?"):
    model = scripted(responses)
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda messages, tools, **kw: model(None, messages, tools, **kw))
    engine.run_agent([{"role": "user", "content": user_text}], max_turns=6)
    return model


def _search_and_record_tools(engine, monkeypatch):
    """Let web_search succeed; record every tool that reaches execution."""
    monkeypatch.setattr(engine, "_local_searxng_no_prompt_enabled", lambda: True)
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: "1. Python 3.14 released")
    executed = []
    real = engine.exec_tool

    def _spy(name, args, **kw):
        executed.append(name)
        return real(name, args, **kw)

    monkeypatch.setattr(engine, "exec_tool", _spy)
    return executed


@pytest.mark.parametrize("command", [
    "curl https://python.org/downloads",
    "wget https://python.org",
    "http https://python.org",
])
def test_web_fetch_shell_after_a_search_is_refused(engine, monkeypatch, scripted, command):
    executed = _search_and_record_tools(engine, monkeypatch)
    _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: execute_shell ✿ARGS✿: {"command": "%s"}' % command,
        "Python 3.14.",
    ])

    assert "execute_shell" not in executed, "a web fetch ran as a search follow-up"


def test_ordinary_shell_after_a_search_still_runs(engine, monkeypatch, scripted):
    """Only fetches are gated — the agent must still be able to do its job."""
    executed = _search_and_record_tools(engine, monkeypatch)
    _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: execute_shell ✿ARGS✿: {"command": "ls -la"}',
        "Done.",
    ])

    assert "execute_shell" in executed, "an ordinary shell command was wrongly blocked"


def test_user_requested_curl_is_still_allowed(engine, monkeypatch, scripted):
    """An explicit instruction outranks the tidiness rule."""
    executed = _search_and_record_tools(engine, monkeypatch)
    _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: execute_shell ✿ARGS✿: {"command": "curl https://python.org"}',
        "Done.",
    ], user_text="run `curl https://python.org` and tell me what you get")

    assert "execute_shell" in executed, "an explicitly requested command was blocked"


def test_shell_before_any_search_is_untouched(engine, monkeypatch, scripted):
    """The gate is about follow-ups; a fetch with no search behind it is fine."""
    executed = _search_and_record_tools(engine, monkeypatch)
    _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: execute_shell ✿ARGS✿: {"command": "curl https://python.org"}',
        "Done.",
    ])

    assert "execute_shell" in executed


# --- MCP -----------------------------------------------------------------

def test_fetch_shaped_mcp_tool_after_a_search_is_refused(engine, monkeypatch,
                                                         scripted, register_tool):
    register_tool("brave_web_search", mode="mcp", mcp_server="brave",
                  mcp_tool="search", args="query")
    executed = _search_and_record_tools(engine, monkeypatch)
    _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: brave_web_search ✿ARGS✿: {"query": "latest python release"}',
        "Python 3.14.",
    ])

    assert "brave_web_search" not in executed


def test_unrelated_mcp_tool_after_a_search_still_runs(engine, monkeypatch,
                                                      scripted, register_tool):
    """Gating by name must not catch MCP tools that do something else entirely."""
    register_tool("github_create_issue", mode="mcp", mcp_server="github",
                  mcp_tool="create_issue", args="title")
    executed = _search_and_record_tools(engine, monkeypatch)
    _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: github_create_issue ✿ARGS✿: {"title": "upgrade python"}',
        "Filed.",
    ])

    assert "github_create_issue" in executed
