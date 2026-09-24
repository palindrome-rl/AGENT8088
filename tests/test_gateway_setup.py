import os
from pathlib import Path
from unittest.mock import patch
import pytest

from agent8088 import cli


def test_gateway_setup_reconfigures_already_active_whatsapp(tmp_path, monkeypatch):
    """When WhatsApp is currently enabled, selecting 'WhatsApp (current)' must

    prompt for settings (e.g. allowed numbers, pairing) and update the config
    without requiring the user to switch to another gateway first.
    """
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "creds.json").write_text('{"paired": true}', encoding="utf-8")
    config_file = tmp_path / "config.txt"
    config_file.write_text(
        "whatsapp_enabled=1\n"
        f"whatsapp_session_dir={session_dir}\n"
        "whatsapp_allowed_users=+921111111111\n"
        "whatsapp_mode=self-chat\n"
        "whatsapp_bridge_port=3000\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AGENT8088_CONFIG", str(config_file))

    # Mock _choice_prompt:
    # 1st call: selects "WhatsApp (current)"
    # 2nd call: selects WhatsApp mode "self-chat"
    def mock_choice(message, choices, default=""):
        if "Select gateway channel" in message:
            for c in choices:
                if c.startswith("WhatsApp"):
                    return c
            return choices[0]
        if "WhatsApp mode" in message:
            return default or "self-chat"
        return choices[0]

    # Mock _custom_prompt:
    # When asked for allowed users, provide updated number +922222222222
    prompts_called = []

    def mock_custom(message, default="", secret=False, instruction=""):
        prompts_called.append(message)
        if "Allowed WhatsApp numbers" in message:
            return "+922222222222"
        if "already paired" in message:
            return "n"
        return default

    with patch.object(cli, "_choice_prompt", side_effect=mock_choice), \
         patch.object(cli, "_custom_prompt", side_effect=mock_custom):
        cli._run_gateway_setup()

    assert any("Allowed WhatsApp numbers" in p for p in prompts_called), (
        f"Expected 'Allowed WhatsApp numbers' prompt to be called, but prompts were: {prompts_called}"
    )

    content = config_file.read_text(encoding="utf-8")
    assert "whatsapp_enabled=1" in content
    assert "whatsapp_allowed_users=+922222222222" in content


def test_gateway_setup_whatsapp_keeps_existing_pairing_when_declined(tmp_path, monkeypatch):
    """If WhatsApp is already paired and user declines re-pairing, existing creds are kept."""
    session_dir = tmp_path / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    creds_file = session_dir / "creds.json"
    creds_file.write_text('{"paired": true}', encoding="utf-8")

    config_file = tmp_path / "config.txt"
    config_file.write_text(
        f"whatsapp_enabled=1\n"
        f"whatsapp_session_dir={session_dir}\n"
        "whatsapp_allowed_users=+921111111111\n"
        "whatsapp_mode=self-chat\n"
        "whatsapp_bridge_port=3000\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AGENT8088_CONFIG", str(config_file))

    def mock_choice(message, choices, default=""):
        if "Select gateway channel" in message:
            for c in choices:
                if c.startswith("WhatsApp"):
                    return c
        return default or choices[0]

    def mock_custom(message, default="", secret=False, instruction=""):
        if "already paired" in message:
            return "n"  # decline re-pair
        return default

    with patch.object(cli, "_choice_prompt", side_effect=mock_choice), \
         patch.object(cli, "_custom_prompt", side_effect=mock_custom):
        cli._run_gateway_setup()

    assert creds_file.exists()
    assert creds_file.read_text(encoding="utf-8") == '{"paired": true}'


def test_gateway_setup_disable_all(tmp_path, monkeypatch):
    """Selecting 'None (disable all)' disables all gateway platforms."""
    config_file = tmp_path / "config.txt"
    config_file.write_text(
        "whatsapp_enabled=1\n"
        "slack_enabled=0\n"
        "discord_enabled=0\n"
        "email_enabled=0\n"
        "telegram_enabled=0\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AGENT8088_CONFIG", str(config_file))

    def mock_choice(message, choices, default=""):
        return "None (disable all)"

    with patch.object(cli, "_choice_prompt", side_effect=mock_choice):
        cli._run_gateway_setup()

    content = config_file.read_text(encoding="utf-8")
    assert "whatsapp_enabled=0" in content
    assert "slack_enabled=0" in content
    assert "discord_enabled=0" in content
    assert "email_enabled=0" in content
    assert "telegram_enabled=0" in content


def test_gateway_setup_reconfigures_already_active_discord(tmp_path, monkeypatch):
    """Selecting Discord (current) prompts for Discord settings and updates allowed users."""
    config_file = tmp_path / "config.txt"
    config_file.write_text(
        "discord_enabled=1\n"
        "discord_allowed_users=user1\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("DISCORD_BOT_TOKEN=old_token\n", encoding="utf-8")

    monkeypatch.setenv("AGENT8088_CONFIG", str(config_file))
    monkeypatch.setattr(cli.A, "ENV_FILE_PATH", env_file)

    def mock_choice(message, choices, default=""):
        for c in choices:
            if c.startswith("Discord"):
                return c
        return default or choices[0]

    prompts_called = []

    def mock_custom(message, default="", secret=False, instruction=""):
        prompts_called.append(message)
        if "Allowed Discord user IDs" in message:
            return "user1,user2"
        return default

    with patch.object(cli, "_choice_prompt", side_effect=mock_choice), \
         patch.object(cli, "_custom_prompt", side_effect=mock_custom):
        cli._run_gateway_setup()

    assert any("Allowed Discord user IDs" in p for p in prompts_called)
    content = config_file.read_text(encoding="utf-8")
    assert "discord_enabled=1" in content
    assert "discord_allowed_users=user1,user2" in content

