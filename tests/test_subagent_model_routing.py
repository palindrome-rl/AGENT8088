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

