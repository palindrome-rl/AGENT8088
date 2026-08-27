import pytest
from pathlib import Path
from agent8088.providers import resolve_subagent_target, MODEL_TIERS

def test_resolve_subagent_target_tier_aliases():
    # Anthropic tier mappings
    prov, model = resolve_subagent_target("haiku", "anthropic")
    assert prov == "anthropic"
    assert model == "claude-haiku-3.5"

    prov, model = resolve_subagent_target("sonnet", "anthropic")
    assert prov == "anthropic"
    assert model == "claude-sonnet-4-6"

    # Gemini tier mappings
    prov, model = resolve_subagent_target("flash", "gemini")
    assert prov == "gemini"
    assert model == "gemini-2.0-flash"

    prov, model = resolve_subagent_target("pro", "gemini")
    assert prov == "gemini"
    assert model == "gemini-2.5-pro"

    # OpenAI tier mappings
    prov, model = resolve_subagent_target("flash", "openai")
    assert prov == "openai"
    assert model == "gpt-4o-mini"

    # DeepSeek tier mappings
    prov, model = resolve_subagent_target("flash", "deepseek")
    assert prov == "deepseek"
    assert model == "deepseek-chat"

    prov, model = resolve_subagent_target("pro", "deepseek")
    assert prov == "deepseek"
    assert model == "deepseek-reasoner"

def test_resolve_subagent_target_inherit_and_empty():
    prov, model = resolve_subagent_target("inherit", "anthropic")
    assert prov == "anthropic"
    assert model == ""

    prov, model = resolve_subagent_target("", "gemini")
    assert prov == "gemini"
    assert model == ""

    prov, model = resolve_subagent_target(None, "openai")
    assert prov == "openai"
    assert model == ""

def test_resolve_subagent_target_cross_provider():
    prov, model = resolve_subagent_target("gemini:gemini-2.0-flash", "anthropic")
    assert prov == "gemini"
    assert model == "gemini-2.0-flash"

    prov, model = resolve_subagent_target("gemini:flash", "anthropic")
    assert prov == "gemini"
    assert model == "gemini-2.0-flash"

    prov, model = resolve_subagent_target("deepseek:pro", "ollama")
    assert prov == "deepseek"
    assert model == "deepseek-reasoner"

def test_resolve_subagent_target_explicit_model_id():
    prov, model = resolve_subagent_target("custom-model-id", "openai")
    assert prov == "openai"
    assert model == "custom-model-id"


def test_load_subagent_specs_parses_model_and_provider(tmp_path):
    from agent8088.engine import load_subagent_specs

    agent_file = tmp_path / "fast-explorer.md"
    agent_file.write_text("""---
name: fast-explorer
description: Fast explorer agent
tools: read_text, execute_shell
max_turns: 5
model: haiku
---
Fast explorer prompt
""", encoding="utf-8")

    specs = load_subagent_specs(tmp_path)
    assert "fast-explorer" in specs
    assert specs["fast-explorer"]["model"] == "haiku"
    assert specs["fast-explorer"]["max_turns"] == 5

    # Test explicit provider:model frontmatter
    cross_file = tmp_path / "gemini-auditor.md"
    cross_file.write_text("""---
name: gemini-auditor
description: Gemini-powered auditor
tools: read_text
max_turns: 4
model: gemini:flash
---
Auditor prompt
""", encoding="utf-8")

    specs_updated = load_subagent_specs(tmp_path)
    assert "gemini-auditor" in specs_updated
    assert specs_updated["gemini-auditor"]["model"] == "gemini:flash"


def test_exec_subagent_scoped_model_routing():
    from unittest.mock import patch
    from agent8088 import engine as eng

    test_specs = {
        "fast_agent": {
            "name": "fast_agent",
            "description": "Fast explorer",
            "tools": ["read_text"],
            "max_turns": 4,
            "permission": "readonly",
            "model": "haiku",
            "provider": "",
            "system_prompt": "You are fast",
        }
    }

    with patch.dict(eng.SUBAGENT_SPECS, test_specs, clear=True), \
         patch("agent8088.engine.load_subagent_specs", return_value=test_specs), \
         patch("agent8088.engine.ACTIVE_PROVIDER", "anthropic"), \
         patch("agent8088.engine.MODEL_NAME", "claude-sonnet-4-6"), \
         patch("agent8088.engine.run_agent") as mock_run:
        mock_run.return_value = "Done"
        res = eng._exec_subagent({"agent_type": "fast_agent", "task": "Search files"})
        assert "Done" in res
        assert mock_run.called
        kwargs = mock_run.call_args.kwargs
        assert kwargs.get("provider_name") == "anthropic"
        assert kwargs.get("model_name") == "claude-haiku-3.5"


def test_exec_subagent_env_override():
    import os
    from unittest.mock import patch
    from agent8088 import engine as eng

    test_specs = {
        "default_agent": {
            "name": "default_agent",
            "description": "Default agent",
            "tools": ["read_text"],
            "max_turns": 4,
            "permission": "readonly",
            "model": "inherit",
            "provider": "",
            "system_prompt": "You are default",
        }
    }

    with patch.dict(os.environ, {"AGENT8088_SUBAGENT_MODEL": "haiku"}), \
         patch.dict(eng.SUBAGENT_SPECS, test_specs, clear=True), \
         patch("agent8088.engine.load_subagent_specs", return_value=test_specs), \
         patch("agent8088.engine.ACTIVE_PROVIDER", "gemini"), \
         patch("agent8088.engine.MODEL_NAME", "gemini-2.5-pro"), \
         patch("agent8088.engine.run_agent") as mock_run:
        mock_run.return_value = "Done"
        res = eng._exec_subagent({"agent_type": "default_agent", "task": "Scan repo"})
        assert "Done" in res
        assert mock_run.called
        kwargs = mock_run.call_args.kwargs
        assert kwargs.get("provider_name") == "gemini"
        assert kwargs.get("model_name") == "gemini-2.0-flash"


def test_builtin_subagent_profiles_loaded():
    from agent8088 import engine as eng
    specs = eng.load_subagent_specs(eng.AGENTS_DIR)
    assert "explore" in specs
    assert specs["explore"]["model"] == "haiku"
    assert "researcher" in specs
    assert specs["researcher"]["model"] == "flash"
    assert "coder" in specs
    assert specs["coder"]["model"] == "inherit"
    assert "auditor" in specs
    assert specs["auditor"]["model"] == "inherit"
    assert "general-purpose" in specs
    assert specs["general-purpose"]["model"] == "inherit"



