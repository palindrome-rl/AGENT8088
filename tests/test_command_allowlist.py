"""Shell command allowlist (allow_commands) and per-turn write blast radius.

allow_commands is the positive counterpart to the existing deny_commands
denylist: a denylist only stops what you thought of, an allowlist stops
everything you did not. Both default to empty/off.
"""
import pytest

from agent8088 import engine as A


# --- allow_commands --------------------------------------------------------

@pytest.fixture
def allowlist(monkeypatch):
    def _set(globs):
        monkeypatch.setattr(A, "_USER_ALLOW_GLOBS", list(globs))
    return _set


def test_empty_allowlist_permits_everything(allowlist):
    allowlist([])
    assert A._outside_user_allowlist("anything --at --all") is False


def test_listed_command_is_permitted(allowlist):
    allowlist(["git status", "ls*", "npm test"])
    assert A._outside_user_allowlist("git status") is False
    assert A._outside_user_allowlist("ls -la") is False
    assert A._outside_user_allowlist("npm test") is False


def test_unlisted_command_is_refused(allowlist):
    allowlist(["git status", "ls*"])
    assert A._outside_user_allowlist("curl https://evil.test") is True
    assert A._outside_user_allowlist("rm -rf build") is True


def test_matching_is_case_insensitive(allowlist):
    allowlist(["git status"])
    assert A._outside_user_allowlist("GIT STATUS") is False


def test_allowlist_is_enforced_at_the_hardline_floor(allowlist):
    """Not escalatable: an unlisted command is refused in every mode."""
    allowlist(["ls*"])
    assert A._hard_blocked_shell("curl https://evil.test") is True
    assert A._hard_blocked_shell("ls -la") is False


def test_denylist_still_wins_over_allowlist(monkeypatch):
    """A command on both lists is refused — deny is the stronger statement."""
    monkeypatch.setattr(A, "_USER_ALLOW_GLOBS", ["git*"])
    monkeypatch.setattr(A, "_USER_DENY_GLOBS", ["git push*"])
    assert A._hard_blocked_shell("git push origin main") is True
    assert A._hard_blocked_shell("git status") is False


def test_allowlist_does_not_unlock_unrecoverable_commands(monkeypatch):
    """Even an explicit allowlist entry cannot re-enable rm -rf /."""
    monkeypatch.setattr(A, "_USER_ALLOW_GLOBS", ["*"])
    assert A._hard_blocked_shell("rm -rf /") is True


def test_allowlist_applies_to_wrapped_payloads(monkeypatch):
    """bash -c '<unlisted>' must not slip past the allowlist."""
    monkeypatch.setattr(A, "_USER_ALLOW_GLOBS", ["ls*"])
    assert A._hard_blocked_shell("bash -c 'curl https://evil.test'") is True


# --- write blast radius ----------------------------------------------------

def test_write_counter_allows_up_to_the_cap(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "MAX_WRITES_PER_TURN", 3)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    engine.reset_turn_counters()
    for i in range(3):
        result = engine.run_tool(
            "write_file", {"filename": str(tmp_path / f"f{i}.txt"), "content": "x"})
        assert "Wrote" in result


def test_write_counter_refuses_past_the_cap(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "MAX_WRITES_PER_TURN", 2)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    engine.reset_turn_counters()
    for i in range(2):
        engine.run_tool("write_file",
                        {"filename": str(tmp_path / f"f{i}.txt"), "content": "x"})
    result = engine.run_tool("write_file",
                             {"filename": str(tmp_path / "f9.txt"), "content": "x"})
    assert "max_writes_per_turn" in result
    assert not (tmp_path / "f9.txt").exists()


def test_write_cap_of_zero_is_disabled(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "MAX_WRITES_PER_TURN", 0)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    engine.reset_turn_counters()
    for i in range(30):
        result = engine.run_tool(
            "write_file", {"filename": str(tmp_path / f"f{i}.txt"), "content": "x"})
        assert "Wrote" in result


def test_oversized_write_is_refused(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "MAX_WRITE_BYTES", 100)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    engine.reset_turn_counters()
    result = engine.run_tool("write_file",
                             {"filename": str(tmp_path / "big.txt"),
                              "content": "x" * 500})
    assert "max_write_bytes" in result
    assert not (tmp_path / "big.txt").exists()


def test_counters_reset_between_turns(engine, monkeypatch, tmp_path):
    """The cap is per turn, so a new turn starts with a full budget."""
    monkeypatch.setattr(engine, "MAX_WRITES_PER_TURN", 1)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    engine.reset_turn_counters()
    assert "Wrote" in engine.run_tool(
        "write_file", {"filename": str(tmp_path / "a.txt"), "content": "x"})
    assert "max_writes_per_turn" in engine.run_tool(
        "write_file", {"filename": str(tmp_path / "b.txt"), "content": "x"})
    engine.reset_turn_counters()
    assert "Wrote" in engine.run_tool(
        "write_file", {"filename": str(tmp_path / "b.txt"), "content": "x"})


def test_run_agent_resets_the_counters(engine, monkeypatch):
    """A fresh turn must not inherit the previous turn's spent write budget."""
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *a, **kw: type("R", (), {
                            "usage": None,
                            "choices": [type("C", (), {
                                "message": type("M", (), {"content": "done"})(),
                                "finish_reason": "stop",
                            })()],
                        })())
    engine._turn_writes = 99
    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=1)
    assert engine._turn_writes == 0


def test_defaults_are_permissive(engine):
    """No new config keys means no behaviour change."""
    assert engine._USER_ALLOW_GLOBS == []
    assert engine.MAX_WRITES_PER_TURN == 0
