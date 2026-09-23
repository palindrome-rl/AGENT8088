"""mem0 embeds with nomic-embed-text on Ollama by default.

That is the same model (and the same Ollama pull) native recall already uses,
so the install carries one embedding model, not two -- the fastembed MiniLM
prefetch is gone from both installers. `memory_mem0_embed_provider=fastembed`
is the explicit opt-out.
"""
import re
from pathlib import Path

from agent8088.memory import parse_memory_engine_config

ROOT = Path(__file__).resolve().parents[1]


def test_mem0_embed_provider_defaults_to_ollama():
    _, cfg = parse_memory_engine_config({})
    assert cfg["embed_provider"] == "ollama"


def test_mem0_embed_provider_follows_the_native_key():
    _, cfg = parse_memory_engine_config({"memory_embed_provider": "openai"})
    assert cfg["embed_provider"] == "openai"


def test_mem0_specific_key_beats_the_native_key():
    _, cfg = parse_memory_engine_config(
        {"memory_embed_provider": "openai", "memory_mem0_embed_provider": "fastembed"})
    assert cfg["embed_provider"] == "fastembed"


def test_mem0_embed_model_stays_empty_so_the_store_picks_per_provider():
    """The store's ollama branch defaults the model to nomic-embed-text and the
    openai branch to text-embedding-3-small; a parse-time model default would
    hand 'nomic-embed-text' to OpenAI."""
    _, cfg = parse_memory_engine_config({})
    assert cfg["embed_model"] == ""


def test_shipped_config_makes_the_default_explicit():
    for name in ("config.txt", "src/agent8088/config.txt"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert re.search(r"(?m)^memory_embed_provider=ollama\s*$", text), name
