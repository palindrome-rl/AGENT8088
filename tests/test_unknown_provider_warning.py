"""An unknown provider name is a config warning (shown under the banner and in
/doctor), not a print at import that lands above the banner -- and the
default_provider typo is warned about once, not twice."""
from agent8088 import engine as A


def test_unknown_provider_is_a_warning_with_a_suggestion_not_a_print(monkeypatch, capsys):
    monkeypatch.delenv("USE_GEMMA4", raising=False)  # another test can leak it
    monkeypatch.setattr(A, "CONFIG_WARNINGS", [])
    monkeypatch.setattr(A, "DEFAULT_PROVIDER", "ollama")
    A.get_client("opnai")
    assert capsys.readouterr().out == ""
    assert len(A.CONFIG_WARNINGS) == 1
    assert "Unknown provider 'opnai' (did you mean 'openai'?); using ollama." in A.CONFIG_WARNINGS[0]


def test_the_default_provider_typo_is_not_warned_twice(monkeypatch, capsys):
    monkeypatch.delenv("USE_GEMMA4", raising=False)  # another test can leak it
    monkeypatch.setattr(A, "CONFIG_WARNINGS", [])
    monkeypatch.setattr(A, "DEFAULT_PROVIDER", "olama")
    A.get_client()
    assert capsys.readouterr().out == ""
    # Already warned where default_provider is read; get_client adds nothing.
    assert A.CONFIG_WARNINGS == []
