"""The setup wizard refuses a working directory that does not exist.

The wizard pre-fills the current value, so pasting a path without clearing the
default produces ".C:\\Users\\..." — one nonsense entry rather than two paths.
Nothing downstream complains: the value saves cleanly and then every write fails
much later with a bare "Path not allowed" that points nowhere near the wizard.
"""
from agent8088 import cli


def test_existing_directory_is_accepted(tmp_path):
    assert cli._invalid_workspace_paths(str(tmp_path)) == []


def test_dot_is_always_valid():
    """'.' means the launch directory, resolved later — not a path to check now."""
    assert cli._invalid_workspace_paths(".") == []


def test_comma_separated_entries_are_checked_individually(tmp_path):
    raw = f"{tmp_path},{tmp_path / 'nope'}"
    assert cli._invalid_workspace_paths(raw) == [str(tmp_path / "nope")]


def test_the_glued_default_is_rejected(tmp_path):
    """The exact shape that broke a real config."""
    glued = f".{tmp_path}"
    assert cli._invalid_workspace_paths(glued) == [glued]


def test_a_file_is_not_a_working_directory(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("x", encoding="utf-8")
    assert cli._invalid_workspace_paths(str(target)) == [str(target)]


def test_prompt_reasks_until_the_path_exists(tmp_path, monkeypatch, capsys):
    answers = iter([f".{tmp_path}", str(tmp_path)])
    monkeypatch.setattr(cli, "_custom_prompt", lambda *_a, **_k: next(answers))

    assert cli._prompt_workspace_paths(".") == str(tmp_path)

    out = capsys.readouterr().out
    assert "Not a directory" in out
    assert "did you mean" in out, "the glued-default case must name the likely fix"


def test_prompt_gives_up_after_a_bounded_number_of_attempts(tmp_path, monkeypatch, capsys):
    """Never loop forever: a non-interactive caller must not hang here."""
    bad = str(tmp_path / "missing")
    calls = []
    monkeypatch.setattr(cli, "_custom_prompt",
                        lambda *_a, **_k: calls.append(1) or bad)

    assert cli._prompt_workspace_paths(".") == bad
    assert len(calls) == cli.WORKSPACE_PROMPT_ATTEMPTS
    assert "Keeping that value" in capsys.readouterr().out


def test_wizard_uses_the_validating_prompt(tmp_path, monkeypatch):
    """Guards the wiring, not just the helper."""
    config = tmp_path / "config.txt"
    config.write_text("allowed_paths=~\ndefault_provider=ollama\n", encoding="utf-8")
    monkeypatch.setenv("AGENT8088_CONFIG", str(config))
    seen = []
    monkeypatch.setattr(cli, "_prompt_workspace_paths",
                        lambda current: seen.append(current) or str(tmp_path))
    monkeypatch.setattr(cli, "_custom_prompt", lambda *_a, **_k: "")
    monkeypatch.setattr(cli, "_searchable_prompt", lambda *_a, **_k: "Keep current setting",
                        raising=False)
    try:
        cli._run_setup()
    except Exception:
        pass  # the wizard's later stages are covered by test_cli_setup.py
    assert seen == ["~"], "the workspace prompt must go through validation"
