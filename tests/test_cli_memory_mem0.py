from unittest.mock import patch
import pytest
from agent8088 import memory

def test_memory_status_reports_active_engine():
    with patch.dict(memory._RUNTIME, {"engine_name": "mem0"}):
        st = memory.status()
        assert st.get("engine") == "mem0"

def test_memory_status_defaults_to_native():
    with patch.dict(memory._RUNTIME, {"engine_name": "native"}):
        st = memory.status()
        assert st.get("engine") == "native"
