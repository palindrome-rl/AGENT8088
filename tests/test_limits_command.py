"""`/limits` changes a limit for this process AND for the next one.

The subtle part is direction. For most budgets 0 means "no limit", so the
numeric direction of a change is the opposite of its safety direction: 0 -> 50
adds a ceiling that was not there, and 50 -> 0 removes it. Comparing numbers
alone would warn on every tightening and stay silent on the one change that
actually deserves the warning.
"""
import pytest

from agent8088 import engine as A


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Point persistence at a throwaway file — never the real config.txt.

    set_limit writes module globals by design, so without restoring them here a
    limit set in one test would silently follow the rest of the suite around.
    """
    path = tmp_path / "config.txt"
    path.write_text("default_provider=ollama\n", encoding="utf-8")
    monkeypatch.setattr(A, "CONFIG_PATH", path)
    monkeypatch.setitem(A.APP_CONFIG, "max_turn_seconds", "0")

    saved_consts = {const: getattr(A, const) for const, _, _ in A.LIMIT_SPECS.values()}
    saved_turns = {n: s["max_turns"] for n, s in A.SUBAGENT_SPECS.items()}
    saved_timeouts = {n: s.get("timeout") for n, s in A.TOOL_SPECS.items()}
    try:
        yield path
    finally:
        for const, value in saved_consts.items():
            setattr(A, const, value)
        for name, turns in saved_turns.items():
            A.SUBAGENT_SPECS[name]["max_turns"] = turns
        for name, timeout in saved_timeouts.items():
            if timeout is not None:
                A.TOOL_SPECS[name]["timeout"] = timeout


# --- Direction ---------------------------------------------------------------

@pytest.mark.parametrize("key,old,new,expected", [
    # 0 == unlimited: adding a ceiling is tighter even though the number grew.
    ("max_turn_seconds", 0, 60, "tighter"),
    ("max_turn_seconds", 60, 0, "looser"),
    ("max_turn_seconds", 60, 120, "looser"),
    ("max_turn_seconds", 120, 60, "tighter"),
    ("max_turn_seconds", 60, 60, "same"),
    # subagent_max_depth has no unlimited sentinel — plain numeric comparison.
    ("subagent_max_depth", 1, 3, "looser"),
    ("subagent_max_depth", 3, 1, "tighter"),
])
def test_direction_is_safety_not_arithmetic(key, old, new, expected):
    assert A.limit_direction(key, old, new) == expected


# --- Applying and persisting -------------------------------------------------

def test_change_applies_to_the_live_process_and_the_file(cfg):
    change = A.set_limit("max_turn_seconds", 45)

    assert A.MAX_TURN_SECONDS == 45          # this process, immediately
    assert "max_turn_seconds=45" in cfg.read_text()   # and the next one
    assert change["direction"] == "tighter"


def test_raising_past_the_soft_ceiling_is_flagged(cfg):
    assert A.set_limit("max_turn_seconds", 60)["over_ceiling"] is False
    assert A.set_limit("max_turn_seconds", 5000)["over_ceiling"] is True


def test_float_limit_keeps_its_precision(cfg):
    assert A.set_limit("max_turn_cost_usd", "2.50")["new"] == 2.5


def test_rejects_unknown_key_and_junk_value(cfg):
    with pytest.raises(KeyError):
        A.set_limit("max_turn_bananas", 5)
    with pytest.raises(ValueError):
        A.set_limit("max_turn_seconds", "soon")
    with pytest.raises(ValueError):
        A.set_limit("max_turn_seconds", -1)
    # A rejected value must not reach the file.
    assert "bananas" not in cfg.read_text()


# --- Sub-agent turns ---------------------------------------------------------

def test_subagent_turns_change_and_persist(cfg):
    change = A.set_subagent_turns("explore", 12)

    assert A.SUBAGENT_SPECS["explore"]["max_turns"] == 12
    assert "subagent_max_turns.explore=12" in cfg.read_text()
    assert change["direction"] == "looser"


def test_persisted_subagent_turns_beat_the_profile_frontmatter(tmp_path, monkeypatch):
    """Otherwise the setting works until you restart, then silently reverts."""
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "explore.md").write_text(
        "---\nname: explore\ntools: read_text\nmax_turns: 6\n---\nbody\n",
        encoding="utf-8")

    monkeypatch.setitem(A.APP_CONFIG, "subagent_max_turns.explore", "12")
    assert A.load_subagent_specs(agents)["explore"]["max_turns"] == 12

    A.APP_CONFIG.pop("subagent_max_turns.explore")
    assert A.load_subagent_specs(agents)["explore"]["max_turns"] == 6


def test_subagent_needs_at_least_one_turn(cfg):
    with pytest.raises(ValueError):
        A.set_subagent_turns("explore", 0)
    with pytest.raises(KeyError):
        A.set_subagent_turns("no-such-profile", 5)


# --- Tool timeouts -----------------------------------------------------------

def test_tool_timeout_changes_and_persists(cfg):
    A.set_tool_timeout("read_text", 40)

    assert A.TOOL_SPECS["read_text"]["timeout"] == 40
    assert "tool_timeout.read_text=40" in cfg.read_text()


def test_persisted_tool_timeout_beats_tools_txt(tmp_path):
    """tools.txt carries an inline timeout=, so without this the override would
    lose on the next start — a setting that only appears to work."""
    spec_file = tmp_path / "tools.txt"
    spec_file.write_text("read_text|Read a file|mode=read_text|args=filename|timeout=10\n",
                         encoding="utf-8")

    plain = A.load_tool_specs(spec_file, {})
    assert plain["read_text"]["timeout"] == 10

    overridden = A.load_tool_specs(spec_file, {"tool_timeout.read_text": "40"})
    assert overridden["read_text"]["timeout"] == 40


def test_tool_timeout_is_bounded(cfg):
    with pytest.raises(ValueError):
        A.set_tool_timeout("read_text", 0)
    with pytest.raises(ValueError):
        A.set_tool_timeout("read_text", A.MAX_TOOL_TIMEOUT_SECONDS + 1)
    with pytest.raises(KeyError):
        A.set_tool_timeout("no_such_tool", 30)


# --- Command wiring ----------------------------------------------------------

def test_limits_command_is_registered():
    from agent8088 import cli
    assert "limits" in cli.COMMANDS


def test_unlimited_is_shown_as_a_word_not_a_zero():
    """A bare 0 reads as 'off by accident' rather than 'deliberately unbounded'."""
    from agent8088 import cli
    assert cli._fmt_limit("max_turn_seconds", 0) == "unlimited"
    assert cli._fmt_limit("max_turn_seconds", 30) == "30"
    # subagent_max_depth=0 is a real value, not a sentinel.
    assert cli._fmt_limit("subagent_max_depth", 0) == "0"
