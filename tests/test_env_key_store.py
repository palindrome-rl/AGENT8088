"""Tests for the .env key store: load, update, migrate, mask, get_secret."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def test_load_env_file_missing_returns_empty(tmp_path):
    from agent8088.engine import load_env_file
    result = load_env_file(tmp_path / "nonexistent.env")
    assert result == {}


def test_load_env_file_reads_key_values(tmp_path):
    from agent8088.engine import load_env_file
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=sk-test123\n# comment\nSLACK_BOT_TOKEN=xoxb-abc\n")
    result = load_env_file(env_path)
    assert result["OPENAI_API_KEY"] == "sk-test123"
    assert result["SLACK_BOT_TOKEN"] == "xoxb-abc"
    assert "comment" not in result


def test_update_env_file_creates_and_writes(tmp_path):
    from agent8088.engine import update_env_file, load_env_file
    env_path = tmp_path / ".env"
    update_env_file(env_path, {"OPENAI_API_KEY": "sk-new"})
    result = load_env_file(env_path)
    assert result["OPENAI_API_KEY"] == "sk-new"


def test_update_env_file_updates_existing(tmp_path):
    from agent8088.engine import update_env_file, load_env_file
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=sk-old\nOTHER=val\n")
    update_env_file(env_path, {"OPENAI_API_KEY": "sk-new"})
    result = load_env_file(env_path)
    assert result["OPENAI_API_KEY"] == "sk-new"
    assert result["OTHER"] == "val"


def test_mask_value_long():
    from agent8088.engine import _mask_value
    assert _mask_value("sk-or-v1-1234567890abcdef") == "sk-...cdef"
    assert _mask_value("xoxb-1234567890-1234") == "xox...1234"


def test_mask_value_short():
    from agent8088.engine import _mask_value
    assert _mask_value("abc") == "(set, too short to mask)"


def test_mask_value_empty():
    from agent8088.engine import _mask_value
    assert _mask_value("") == "(not set yet)"


def test_get_secret_from_env_file(tmp_path, monkeypatch):
    from agent8088.engine import get_secret, load_env_file, ENV_FILE_PATH
    env_path = tmp_path / ".env"
    env_path.write_text("DISCORD_BOT_TOKEN=test-token-12345\n")
    config = {"discord_bot_token_env": "DISCORD_BOT_TOKEN"}
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", env_path)
    result = get_secret(config, "discord_bot_token")
    assert result == "test-token-12345"


def test_get_secret_fallback_to_config(tmp_path, monkeypatch):
    from agent8088.engine import get_secret
    env_path = tmp_path / ".env"
    env_path.write_text("")
    config = {"discord_bot_token": "fallback-token"}
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", env_path)
    result = get_secret(config, "discord_bot_token")
    assert result == "fallback-token"


def test_migrate_keys_to_env_moves_api_keys(tmp_path):
    from agent8088.engine import _migrate_keys_to_env, load_env_file, load_simple_config
    config_path = tmp_path / "config.txt"
    env_path = tmp_path / ".env"
    config_path.write_text(
        "default_provider=openrouter\n"
        "provider.openrouter.api_key=sk-or-test123\n"
        "provider.openrouter.model=qwen\n"
    )
    migrated = _migrate_keys_to_env(config_path, env_path)
    assert migrated == 1
    env = load_env_file(env_path)
    assert env["OPENROUTER_API_KEY"] == "sk-or-test123"
    config = load_simple_config(config_path)
    assert config.get("provider.openrouter.api_key", "") == ""
    assert config.get("provider.openrouter.api_key_env") == "OPENROUTER_API_KEY"


def test_migrate_keys_to_env_moves_gateway_tokens(tmp_path):
    from agent8088.engine import _migrate_keys_to_env, load_env_file
    config_path = tmp_path / "config.txt"
    env_path = tmp_path / ".env"
    config_path.write_text(
        "discord_enabled=1\n"
        "discord_bot_token=MjQtest123456\n"
        "slack_bot_token=xoxb-test123456\n"
    )
    migrated = _migrate_keys_to_env(config_path, env_path)
    assert migrated == 2
    env = load_env_file(env_path)
    assert env["DISCORD_BOT_TOKEN"] == "MjQtest123456"
    assert env["SLACK_BOT_TOKEN"] == "xoxb-test123456"


def test_migrate_keys_to_env_skips_if_env_exists(tmp_path):
    from agent8088.engine import _migrate_keys_to_env
    config_path = tmp_path / "config.txt"
    env_path = tmp_path / ".env"
    config_path.write_text("provider.openrouter.api_key=sk-test\n")
    env_path.write_text("EXISTING=val\n")
    migrated = _migrate_keys_to_env(config_path, env_path)
    assert migrated == 0


def test_provider_api_key_reads_from_env_file(tmp_path, monkeypatch):
    from agent8088.engine import _provider_api_key, ENV_FILE_PATH
    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_API_KEY=sk-from-env\n")
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", env_path)
    provider = {"api_key_env": "OPENROUTER_API_KEY"}
    assert _provider_api_key(provider) == "sk-from-env"


def test_provider_api_key_fallback_to_direct(tmp_path, monkeypatch):
    from agent8088.engine import _provider_api_key, ENV_FILE_PATH
    env_path = tmp_path / ".env"
    env_path.write_text("")
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", env_path)
    provider = {"api_key": "sk-direct", "api_key_env": "NONEXISTENT"}
    assert _provider_api_key(provider) == "sk-direct"