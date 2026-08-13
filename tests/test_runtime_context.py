"""The runtime-context block: the model's only source of "today".

Without it the model has no clock, only a training cutoff. It cannot
date-qualify a search, cannot tell a stale page from a current one, and reads
"the next election" as whatever was next while it was trained.
"""
from datetime import datetime, timezone

MOMENT = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def test_runtime_context_states_the_date(engine):
    block = engine.render_runtime_context(now=MOMENT)

    assert "Monday, 10 August 2026" in block
    assert "2026" in block
    assert "August 2026" in block


def test_runtime_context_warns_against_answering_from_memory(engine):
    block = engine.render_runtime_context(now=MOMENT)

    assert "search" in block.lower()
    assert "training" in block.lower()


def test_runtime_context_defaults_to_today(engine):
    assert str(datetime.now().astimezone().year) in engine.render_runtime_context()


def test_cli_session_prompt_carries_the_date():
    from agent8088 import cli

    assert "Runtime Context" in cli._session_system_prompt()


def test_gateway_prompt_carries_the_date():
    """The gateway builds its own prompt; it must not miss the date."""
    from agent8088.gateway import agent_bridge

    assert "Runtime Context" in agent_bridge.build_system_prompt()


def test_default_system_prompt_is_rebuilt_per_call(engine):
    """A process that runs for days must not keep the date it booted with.

    SYSTEM_PROMPT is built once at import — fine for a CLI invocation, wrong
    for the gateway and cron, which stay up long enough for the date to move
    underneath them.
    """
    engine.SYSTEM_PROMPT = "STALE PROMPT with no date in it"

    assert "Runtime Context" in engine.current_system_prompt()
    assert "STALE PROMPT" in engine.current_system_prompt()


def test_current_system_prompt_does_not_stack_context_blocks(engine):
    """Calling it twice must not append two Runtime Context sections."""
    engine.SYSTEM_PROMPT = "base" + engine.render_runtime_context(now=MOMENT)

    once = engine.current_system_prompt()

    assert once.count("## Runtime Context") == 1
