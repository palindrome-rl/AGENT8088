"""Smart approval: an auxiliary model auto-approves low-risk gated actions.

Hermes' `approvals.mode: smart` — a guardian LLM assesses risk, auto-approving
low-risk commands and escalating uncertain or dangerous ones, with
`smart_approval_policy` appended to its system prompt for environment-specific
judgement.

The load-bearing property, and the reason Hermes' own SECURITY.md calls this a
heuristic rather than a boundary: the guardian may only ever *skip a prompt the
operator would have answered*. It can never widen what is reachable, and it never
sees or bypasses the always-on floor.
"""
import pytest

from agent8088 import engine as A


@pytest.fixture
def smart(engine, monkeypatch):
    monkeypatch.setattr(engine, "APPROVAL_MODE", "smart")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    return engine


def _verdict(engine, monkeypatch, text):
    """Stub the guardian's model call with a fixed reply."""
    seen = {}

    def _fake(messages, tools, **kw):
        seen["messages"] = messages
        seen["system"] = kw.get("system_prompt", "")
        return type("R", (), {
            "usage": None,
            "choices": [type("C", (), {
                "message": type("M", (), {"content": text})(),
                "finish_reason": "stop",
            })()],
        })()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    return seen


# --- The verdict parser ----------------------------------------------------

@pytest.mark.parametrize("reply", ["APPROVE", "approve", " APPROVE ", "APPROVE: looks fine"])
def test_approve_verdicts_are_recognised(engine, reply):
    assert engine._parse_guardian_verdict(reply) is True


@pytest.mark.parametrize("reply", ["DENY", "deny", "ESCALATE", "escalate: unsure"])
def test_deny_and_escalate_verdicts_are_recognised(engine, reply):
    assert engine._parse_guardian_verdict(reply) is False


@pytest.mark.parametrize("reply", ["", "   ", "maybe?", "I think it's fine", None,
                                   "the user should decide"])
def test_unparseable_verdict_fails_closed(engine, reply):
    """An ambiguous guardian reply must escalate, never auto-approve."""
    assert engine._parse_guardian_verdict(reply) is False


# --- The gate --------------------------------------------------------------

def test_low_risk_action_is_auto_approved(smart, monkeypatch):
    _verdict(smart, monkeypatch, "APPROVE")
    monkeypatch.setattr(smart, "_exec_shell_command", lambda *a, **kw: "OK")
    monkeypatch.setattr(smart, "_resolve_sandbox_backend", lambda: "native")
    # `mkdir build` is gated in readonly (not on the safe-inspection list), so it
    # reaches the guardian rather than being permitted outright.
    result = smart.run_tool("execute_shell", {"command": "mkdir build"})
    assert not result.startswith("ESCALATION_REQUEST:")


def test_readonly_safe_command_never_reaches_the_guardian(smart, monkeypatch):
    """Already-permitted actions must not cost a guardian call."""
    def _must_not_run(*a, **kw):
        raise AssertionError("guardian consulted for an already-permitted command")

    monkeypatch.setattr(smart, "_create_completion_with_fallback", _must_not_run)
    monkeypatch.setattr(smart, "_exec_shell_command", lambda *a, **kw: "OK")
    monkeypatch.setattr(smart, "_resolve_sandbox_backend", lambda: "native")
    smart.run_tool("execute_shell", {"command": "ls -la"})


def test_risky_action_still_escalates_to_the_operator(smart, monkeypatch):
    _verdict(smart, monkeypatch, "DENY")
    result = smart.run_tool("execute_shell", {"command": "rm -rf build"})
    assert result.startswith("ESCALATION_REQUEST:")


def test_guardian_failure_escalates(smart, monkeypatch):
    """If the guardian errors, fall back to asking the operator."""
    def _boom(*a, **kw):
        raise RuntimeError("aux model unreachable")

    monkeypatch.setattr(smart, "_create_completion_with_fallback", _boom)
    result = smart.run_tool("execute_shell", {"command": "rm -rf build"})
    assert result.startswith("ESCALATION_REQUEST:")


def test_manual_mode_never_calls_the_guardian(engine, monkeypatch):
    monkeypatch.setattr(engine, "APPROVAL_MODE", "manual")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")

    def _must_not_run(*a, **kw):
        raise AssertionError("guardian must not be consulted in manual mode")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _must_not_run)
    assert engine.run_tool(
        "execute_shell", {"command": "rm -rf build"}).startswith("ESCALATION_REQUEST:")


def test_off_mode_skips_the_gate_entirely(engine, monkeypatch):
    monkeypatch.setattr(engine, "APPROVAL_MODE", "off")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine, "_exec_shell_command", lambda *a, **kw: "OK")
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    assert not engine.run_tool(
        "execute_shell", {"command": "rm -rf build"}).startswith("ESCALATION_REQUEST:")


# --- The guardian cannot widen reach ---------------------------------------

def test_guardian_cannot_unlock_the_always_on_floor(smart, monkeypatch):
    """APPROVE on an unrecoverable command must change nothing."""
    _verdict(smart, monkeypatch, "APPROVE")
    assert "forbidden" in smart.run_tool(
        "execute_shell", {"command": "rm -rf /"}).lower()


def test_guardian_is_not_consulted_for_floor_refusals(smart, monkeypatch):
    """The floor decides before the guardian is even asked — no tokens wasted."""
    def _must_not_run(*a, **kw):
        raise AssertionError("guardian must not see a floor-refused command")

    monkeypatch.setattr(smart, "_create_completion_with_fallback", _must_not_run)
    smart.run_tool("execute_shell", {"command": "rm -rf /"})


def test_guardian_cannot_unlock_sensitive_file_writes(smart, monkeypatch, tmp_path):
    _verdict(smart, monkeypatch, "APPROVE")
    monkeypatch.setattr(smart, "ALLOWED_PATHS", [tmp_path])
    result = smart.run_tool("write_file",
                            {"filename": str(tmp_path / ".env"), "content": "x"})
    assert "sensitive file denied" in result


def test_guardian_cannot_unlock_an_outbound_secret(smart, monkeypatch):
    secret = "sk-live-abcdef0123456789"
    _verdict(smart, monkeypatch, "APPROVE")
    monkeypatch.setattr(smart, "_SECRET_VALUES", [secret])
    result = smart.run_tool("web_search_tavily", {"query": secret})
    assert "credential" in result.lower()


# --- Prompt construction ---------------------------------------------------

def test_guardian_prompt_contains_the_action_and_mode(smart, monkeypatch):
    seen = _verdict(smart, monkeypatch, "DENY")
    smart.run_tool("execute_shell", {"command": "rm -rf build"})
    blob = seen["system"] + str(seen["messages"])
    assert "rm -rf build" in blob
    assert "execute_shell" in blob


def test_operator_policy_is_appended_to_the_guardian_prompt(smart, monkeypatch):
    monkeypatch.setattr(smart, "SMART_APPROVAL_POLICY",
                        "This box is a scratch VM; tolerate rm under /tmp.")
    seen = _verdict(smart, monkeypatch, "DENY")
    smart.run_tool("execute_shell", {"command": "rm -rf /tmp/x"})
    assert "scratch VM" in seen["system"] + str(seen["messages"])


def test_guardian_sees_untrusted_markers_around_the_command(smart, monkeypatch):
    """The command is model-authored text, so the guardian is told so."""
    seen = _verdict(smart, monkeypatch, "DENY")
    smart.run_tool("execute_shell", {"command": "rm -rf build"})
    blob = seen["system"] + str(seen["messages"])
    assert "UNTRUSTED" in blob.upper()


def test_guardian_decision_is_audited(smart, monkeypatch, tmp_path):
    import json
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(smart, "AUDIT_LOG_PATH", path)
    monkeypatch.setattr(smart, "AUDIT_ENABLED", True)
    monkeypatch.setattr(smart, "_exec_shell_command", lambda *a, **kw: "OK")
    monkeypatch.setattr(smart, "_resolve_sandbox_backend", lambda: "native")
    _verdict(smart, monkeypatch, "APPROVE")
    # Must be a command that actually reaches the gate — a readonly-safe one like
    # `ls -la` is already permitted, so the guardian is never consulted.
    smart.run_tool("execute_shell", {"command": "rm -rf build"})
    entries = [json.loads(l) for l in path.read_text().splitlines()]
    assert any(e.get("reason") == "smart_approved" for e in entries)


def test_unattended_run_does_not_use_the_guardian(smart, monkeypatch):
    """cron_mode is the policy for unattended runs; don't stack a second one."""
    monkeypatch.setattr(smart, "UNATTENDED", True)
    monkeypatch.setattr(smart, "CRON_MODE", "deny")

    def _must_not_run(*a, **kw):
        raise AssertionError("guardian must not run unattended")

    monkeypatch.setattr(smart, "_create_completion_with_fallback", _must_not_run)
    assert "unattended" in smart.run_tool(
        "execute_shell", {"command": "rm -rf build"}).lower()
