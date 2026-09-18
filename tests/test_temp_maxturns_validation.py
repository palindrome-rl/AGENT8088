"""Range validation for /temp and /maxturns.

Live REPL testing (01-basic:479-489) showed /temp -1, /temp 99, /maxturns 0 and
/maxturns -5 were all accepted and written to the saved config. Only
non-numeric input was rejected. These commands persist their values, so an
out-of-range value that gets accepted survives into every future session.
"""
from agent8088 import cli


def _run(rest):
    with cli.console.capture() as capture:
        cli.cmd_temp(rest)
    return capture.get()


def _run_maxturns(rest):
    with cli.console.capture() as capture:
        cli.cmd_maxturns(rest)
    return capture.get()


def _saved_value(monkeypatch):
    saved = {}
    monkeypatch.setattr(cli, "_save_preferences", lambda: saved.update(
        temperature=cli.S.temperature, max_turns=cli.S.max_turns))
    return saved


# --- /temp ----------------------------------------------------------------

def test_temp_below_zero_is_rejected(monkeypatch):
    saved = _saved_value(monkeypatch)
    out = _run("-1")
    assert "0.0 to 2.0" in out
    assert saved == {}  # nothing persisted


def test_temp_above_two_is_rejected(monkeypatch):
    saved = _saved_value(monkeypatch)
    out = _run("99")
    assert "0.0 to 2.0" in out
    assert saved == {}


def test_temp_just_out_of_range_is_rejected(monkeypatch):
    saved = _saved_value(monkeypatch)
    out = _run("2.001")
    assert "0.0 to 2.0" in out
    assert saved == {}


def test_temp_boundary_values_are_accepted(monkeypatch):
    saved = _saved_value(monkeypatch)
    for value in ("0", "0.0", "2.0"):
        out = _run(value)
        assert "temperature" in out
        assert "0.0 to 2.0" not in out
    assert saved["temperature"] == 2.0


def test_temp_mid_range_is_accepted(monkeypatch):
    saved = _saved_value(monkeypatch)
    out = _run("0.7")
    assert "temperature" in out
    assert "0.0 to 2.0" not in out
    assert saved["temperature"] == 0.7


def test_temp_non_numeric_still_rejected(monkeypatch):
    saved = _saved_value(monkeypatch)
    out = _run("hot")
    assert "usage" in out
    assert saved == {}


# --- /maxturns ------------------------------------------------------------

def test_maxturns_zero_is_rejected(monkeypatch):
    saved = _saved_value(monkeypatch)
    out = _run_maxturns("0")
    assert "at least 1" in out
    assert saved == {}


def test_maxturns_negative_is_rejected(monkeypatch):
    saved = _saved_value(monkeypatch)
    out = _run_maxturns("-5")
    assert "at least 1" in out
    assert saved == {}


def test_maxturns_one_is_accepted(monkeypatch):
    saved = _saved_value(monkeypatch)
    out = _run_maxturns("1")
    assert "max_turns" in out
    assert "at least 1" not in out
    assert saved["max_turns"] == 1


def test_maxturns_large_is_accepted(monkeypatch):
    saved = _saved_value(monkeypatch)
    out = _run_maxturns("200")
    assert "at least 1" not in out
    assert saved["max_turns"] == 200


def test_maxturns_non_numeric_still_rejected(monkeypatch):
    saved = _saved_value(monkeypatch)
    out = _run_maxturns("many")
    assert "usage" in out
    assert saved == {}