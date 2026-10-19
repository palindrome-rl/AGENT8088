"""Offline release checks: explicit network opt-in and approval boundaries."""
import importlib
from contextlib import nullcontext

import pytest


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "absent.txt"))
    monkeypatch.setenv("AGENT8088_DOCKER_AUTOSTART", "0")
    monkeypatch.setenv("AGENT8088_SANDBOX", "local")
    from agent8088 import engine
    return importlib.reload(engine)


def test_open_network_is_not_a_shipped_default(runtime):
    assert not runtime.SANDBOX_NETWORK_OPEN
    assert runtime.PLAN_MODE_RETRY_LIMIT == 4


@pytest.mark.parametrize("platform,network", [("linux", "host"), ("darwin", "bridge"), ("win32", "bridge")])
def test_platform_network_selection(runtime, monkeypatch, platform, network):
    monkeypatch.setattr(runtime.sys, "platform", platform)
    assert runtime._open_docker_network() == network


@pytest.mark.parametrize("user_limit,remaining", [(0, 0), (300, 300)])
def test_approval_retains_explicit_user_time_limit(runtime, monkeypatch, user_limit, remaining):
    budget = runtime._TurnBudget(max_seconds=300)
    monkeypatch.setattr(runtime, "_active_budget", budget)
    monkeypatch.setattr(runtime, "MAX_TURN_SECONDS", user_limit)
    monkeypatch.setattr(runtime, "PLAN_MODE_TIMEOUT_SECONDS", 300)
    runtime._lift_plan_clock()
    assert budget.max_seconds == remaining


def test_rejected_proposal_does_not_replace_user_plan(runtime, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(runtime, "BLOCKED_PATHS", [])
    monkeypatch.setattr(runtime, "PERMISSION_MODE", "plan-only")
    monkeypatch.setattr(runtime, "_human_wait", nullcontext)
    monkeypatch.setattr(runtime, "_plan_on_approval", lambda text: None)
    original = tmp_path / "PLAN.md"
    original.write_text("keep this plan", encoding="utf-8")
    result = runtime._exec_present_plan({"plan": "An unapproved proposal"})
    assert original.read_text(encoding="utf-8") == "keep this plan"
    assert len(list(tmp_path.glob("PLAN.proposal-*.md"))) == 1
    assert "No plan steps were run" in result


def test_autostart_opt_out_never_launches_desktop(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "_docker_available", lambda: False)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: pytest.fail("launched Docker"))
    assert not runtime._start_docker_desktop()


def test_mcp_offering_does_not_add_unpermitted_tools(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "TOOL_SPECS", {
        "mcp_read": {"mode": "mcp"}, "mcp_write": {"mode": "mcp"},
        "read_file": {"mode": "read_text"},
    })
    assert runtime._mcp_tool_names({"mcp_read", "read_file"}) == {"mcp_read"}
