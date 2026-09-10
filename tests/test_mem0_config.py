import pytest
from agent8088.memory import parse_memory_engine_config

def test_parse_memory_engine_default():
    config = {}
    engine_name, mem0_cfg = parse_memory_engine_config(config)
    assert engine_name == "native"
    assert mem0_cfg["vector_store"] == "qdrant"
    assert mem0_cfg["disable_telemetry"] is True

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
