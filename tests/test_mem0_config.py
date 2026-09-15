import pytest
from agent8088.memory import parse_memory_engine_config

def test_parse_memory_engine_default():
    config = {}
    engine_name, mem0_cfg = parse_memory_engine_config(config)
    assert engine_name == "native"
    assert mem0_cfg["vector_store"] == "qdrant"
    assert mem0_cfg["disable_telemetry"] is True

def test_load_simple_config_strips_a_utf8_bom(tmp_path):
    """Windows PowerShell 5 writes config.txt with a UTF-8 BOM; the BOM glues
    itself to the first key, so \\ufeffmemory_engine stops matching memory_engine
    and the installer's engine choice silently falls to native. Any Windows
    editor can re-add a BOM, so the loader must tolerate one."""
    from agent8088.engine import load_simple_config
    config = tmp_path / "config.txt"
    config.write_bytes(b"\xef\xbb\xbfmemory_engine=mem0\nmemory=1\n")
    parsed = load_simple_config(config)
    assert parsed.get("memory_engine") == "mem0"
    assert parsed.get("memory") == "1"

def test_parse_memory_engine_mem0_selected():
    config = {
        "memory_engine": "mem0",
        "memory_mem0_vector_store": "qdrant",
        "memory_mem0_dir": "~/.agent8088/custom_mem0",
    }
    engine_name, mem0_cfg = parse_memory_engine_config(config)
    assert engine_name == "mem0"
    assert "custom_mem0" in mem0_cfg["path"]
    assert mem0_cfg["disable_telemetry"] is True

def test_mem0_default_dir_follows_agent8088_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    engine_name, mem0_cfg = parse_memory_engine_config({"memory_engine": "mem0"})
    assert mem0_cfg["path"] == str(tmp_path / "home" / "mem0")

def test_mem0_explicit_dir_beats_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    engine_name, mem0_cfg = parse_memory_engine_config(
        {"memory_mem0_dir": str(tmp_path / "custom")})
    assert mem0_cfg["path"] == str(tmp_path / "custom")
