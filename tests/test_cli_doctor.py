"""Regression tests for cmd_doctor.

The Authentication row used to read os.environ.get(key_env), which reported
"missing" for any key that lives in the .env key store the setup wizard
writes to — even though _provider_api_key() (the resolver model calls use)
sees it fine. The fix routes the check through _provider_api_key so /doctor
matches actual runtime behaviour.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent8088 import cli, engine as A


def _configure(tmp_path, monkeypatch, provider, key_env, env_contents):
    """Point cli.A at an isolated config + .env under tmp_path."""
    config = tmp_path / "config.txt"
    config.write_text(
        f"default_provider={provider}\n"
        f"provider.{provider}.base_url=http://localhost:11434/v1\n"
        f"provider.{provider}.model=m\n"
        f"provider.{provider}.api_key_env={key_env}\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(env_contents, encoding="utf-8")

    monkeypatch.setattr(cli.A, "CONFIG_PATH", config)
    monkeypatch.setattr(cli.A, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(cli.A, "APP_CONFIG", A.load_simple_config(config))
    monkeypatch.setattr(cli.A, "PROVIDERS", A.load_providers(cli.A.APP_CONFIG))
    monkeypatch.setattr(cli.A, "ACTIVE_PROVIDER", provider)
    monkeypatch.setattr(cli.A, "MODEL_NAME", "m")
    monkeypatch.setattr(cli, "banner", lambda: None)


def test_doctor_auth_shows_set_when_key_lives_only_in_env_store(capsys, monkeypatch, tmp_path):
    """The regression: key is in .env, NOT in os.environ -> must still say 'set'."""
    monkeypatch.delenv("ACME_API_KEY", raising=False)
    _configure(tmp_path, monkeypatch, "acme", "ACME_API_KEY",
               "ACME_API_KEY=sk-from-store\n")

    cli.cmd_doctor("")
    out = capsys.readouterr().out

    assert "ACME_API_KEY: set" in out
    assert "ACME_API_KEY: missing" not in out


def test_doctor_auth_shows_missing_when_key_is_nowhere(capsys, monkeypatch, tmp_path):
    """Counter-check: no .env entry, no os.environ, no config api_key -> 'missing'."""
    monkeypatch.delenv("GHOST_API_KEY", raising=False)
    _configure(tmp_path, monkeypatch, "ghost", "GHOST_API_KEY", "")

    cli.cmd_doctor("")
    out = capsys.readouterr().out

    assert "GHOST_API_KEY: missing" in out