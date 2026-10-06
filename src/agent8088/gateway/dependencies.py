"""Which enabled gateway adapters cannot load, and why.

An adapter whose Python dependency is missing used to produce only a warning
in the log file: the gateway started, the operator saw nothing, and Slack (say)
just never answered. This module names each such adapter through the
capabilities registry (capabilities.GATEWAY) so the gateway's startup notice,
/doctor and /status all report it the same way.

Cheap and import-free: dependencies are checked with importlib.util.find_spec,
which locates a package without importing it, so /doctor can call this too.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass

from agent8088 import capabilities

GATEWAY_FIX = 'uv pip install -e ".[gateway]"'


@dataclass(frozen=True)
class AdapterSpec:
    platform: str        # "slack"
    config_key: str      # "slack_enabled"
    module: str          # adapter module, imported only by the runner
    cls: str             # adapter class name in that module
    dependency: str      # top-level import the adapter needs ("" = stdlib)
    package: str         # what to pip install for it


ADAPTERS = (
    AdapterSpec("slack", "slack_enabled", "agent8088.gateway.platforms.slack",
                "SlackAdapter", "slack_bolt", "slack-bolt"),
    AdapterSpec("whatsapp", "whatsapp_enabled", "agent8088.gateway.platforms.whatsapp",
                "WhatsAppAdapter", "httpx", "httpx"),
    AdapterSpec("discord", "discord_enabled", "agent8088.gateway.platforms.discord",
                "DiscordAdapter", "discord", "discord.py"),
    AdapterSpec("email", "email_enabled", "agent8088.gateway.platforms.email",
                "EmailAdapter", "", ""),
    AdapterSpec("telegram", "telegram_enabled", "agent8088.gateway.platforms.telegram",
                "TelegramAdapter", "telegram", "python-telegram-bot"),
)

_TRUE = ("1", "true", "True")


def enabled_adapters(config) -> list[AdapterSpec]:
    return [spec for spec in ADAPTERS if str(config.get(spec.config_key, "0")) in _TRUE]


def dependency_missing(spec: AdapterSpec) -> bool:
    if not spec.dependency:
        return False
    try:
        return importlib.util.find_spec(spec.dependency) is None
    except (ImportError, ValueError):
        return True


def disabled_reason(spec: AdapterSpec, error: BaseException | None = None) -> str:
    if spec.package and (error is None or dependency_missing(spec)):
        return f"{spec.package} not installed"
    return f"failed to import ({error})" if error else "failed to import"


def report(config, disabled: list[tuple[AdapterSpec, str]] | None = None) -> list[tuple[AdapterSpec, str]]:
    """Report capabilities.GATEWAY for the enabled adapters; return the disabled.

    `disabled` is what the runner actually failed to load, as (spec, reason).
    Without it (the /doctor path) each enabled adapter's dependency is located
    with find_spec instead. No adapter enabled: the gateway is not in use, so
    the entry is cleared rather than reported."""
    enabled = enabled_adapters(config)
    if not enabled:
        capabilities.clear(capabilities.GATEWAY)
        return []
    if disabled is None:
        disabled = [(spec, disabled_reason(spec)) for spec in enabled if dependency_missing(spec)]
    names = [spec.platform for spec in enabled]
    working = [name for name in names if name not in {spec.platform for spec, _ in disabled}]
    if not disabled:
        capabilities.report(capabilities.GATEWAY, active=", ".join(working),
                            preferred=", ".join(names), state=capabilities.OK)
        return []
    capabilities.report(
        capabilities.GATEWAY,
        active=", ".join(working),
        preferred=", ".join(names),
        state=capabilities.DEGRADED if working else capabilities.UNAVAILABLE,
        reason="; ".join(f"{spec.platform}: {why}" for spec, why in disabled),
        impact="; ".join(f"{spec.platform} adapter disabled" for spec, _ in disabled),
        fix=GATEWAY_FIX if any(spec.package for spec, _ in disabled) else "check config.txt",
    )
    return disabled


def startup_notice(disabled: list[tuple[AdapterSpec, str]]) -> str:
    """One line for the gateway's startup output, or "" when none is disabled."""
    if not disabled:
        return ""
    parts = ", ".join(f"{spec.platform} ({why})" for spec, why in disabled)
    fix = f" — fix: {GATEWAY_FIX}" if any(spec.package for spec, _ in disabled) else ""
    return f"gateway: adapters disabled: {parts}{fix}"
