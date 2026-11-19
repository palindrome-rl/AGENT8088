from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from agent8088 import memory
from agent8088.memory.mem0_store import Mem0MemoryStore


def test_memory_status_reports_configured_engine():
    with patch.dict(memory._RUNTIME, {"engine_name": "mem0"}):
        st = memory.status()
        assert st.get("engine_configured") == "mem0"


def test_memory_status_defaults_to_native():
    with patch.dict(memory._RUNTIME, {"engine_name": "native"}):
        st = memory.status()
        assert st.get("engine_configured") == "native"


# --- the live engine is what the store says it is, not what config asked for ---


def test_status_reports_native_when_mem0_falls_back():
    """mem0 configured but unavailable must not be reported as the live engine.

    store() degrades to the native store silently; saying "mem0" here would tell
    the user their memories are in Qdrant when they are in SQLite.
    """
    native = MagicMock()
    native.count.return_value = 3
    del native.status
    with patch.dict(memory._RUNTIME, {"enabled": True, "engine_name": "mem0",
                                      "db_path": "/tmp/x.db", "store": native}, clear=False):
        st = memory.status()
        assert st["engine"] == "native"
        assert st["engine_configured"] == "mem0"


def test_status_carries_the_reason_mem0_fell_back(capsys):
    """/memory must name the reason, not just the fact.

    "mem0 was requested but is unavailable" with no reason is what made the
    unsupported-provider bug take three machines and a local repro to find.
    """
    from agent8088 import cli
    native = MagicMock()
    native.count.return_value = 0
    native.path.exists.return_value = False
    del native.status
    with patch.dict(memory._RUNTIME,
                    {"enabled": True, "engine_name": "mem0", "db_path": "/tmp/x.db",
                     "store": native, "mem0_error": "Unsupported LLM provider: custom"},
                    clear=False):
        report = memory.status()
        assert report["engine"] == "native"
        assert report["engine_error"] == "Unsupported LLM provider: custom"
        cli._show_memory_status()
    assert "Unsupported LLM provider" in capsys.readouterr().out


def test_status_has_no_reason_when_native_is_the_deliberate_choice():
    native = MagicMock()
    native.count.return_value = 0
    del native.status
    with patch.dict(memory._RUNTIME,
                    {"enabled": True, "engine_name": "native", "db_path": "/tmp/x.db",
                     "store": native}, clear=False):
        assert memory.status()["engine_error"] == ""


def test_status_reports_mem0_when_store_is_mem0():
    mem0 = MagicMock()
    mem0.count.return_value = 7
    mem0.status.return_value = {
        "engine": "mem0",
        "embedder": {"provider": "fastembed",
                     "model": "sentence-transformers/all-MiniLM-L6-v2", "dims": 384},
    }
    with patch.dict(memory._RUNTIME, {"enabled": True, "engine_name": "mem0",
                                      "db_path": "/tmp/x.db", "store": mem0}, clear=False):
        st = memory.status()
        assert st["engine"] == "mem0"
        assert st["engine_configured"] == "mem0"
        assert st["embedder_info"]["provider"] == "fastembed"
        assert st["embedder_info"]["dims"] == 384


# --- mem0 records the embedder it actually resolved ---


def test_mem0_store_records_resolved_embedder(tmp_path):
    store = Mem0MemoryStore({"path": str(tmp_path), "embed_provider": "ollama",
                             "embed_model": "nomic-embed-text"})
    fake_memory_cls = MagicMock()
    fake_memory_cls.from_config.return_value = MagicMock()
    with patch("agent8088.memory.mem0_store._import_mem0", return_value=fake_memory_cls):
        assert store.available()
    assert store.embedder_info == {"provider": "ollama",
                                   "model": "nomic-embed-text", "dims": 768}
    assert store.status()["embedder"]["provider"] == "ollama"


def test_mem0_store_embedder_info_reaches_status_before_init(tmp_path):
    """status() must not blow up when the engine was never initialized."""
    store = Mem0MemoryStore({"path": str(tmp_path)})
    with patch("agent8088.memory.mem0_store._import_mem0", return_value=None):
        assert store.status()["embedder"] is None


# --- /memory engine ---


def test_set_engine_writes_config_and_reconfigures():
    from agent8088 import cli
    with patch("agent8088.engine.update_simple_config") as write, \
         patch("agent8088.engine.configure_memory") as reconfigure, \
         patch.dict("agent8088.engine.APP_CONFIG", {}, clear=False):
        cli._memory_set_engine("mem0")
        write.assert_called_once()
        assert write.call_args[0][1] == {"memory_engine": "mem0"}
        reconfigure.assert_called_once()
        from agent8088 import engine as A
        assert A.APP_CONFIG["memory_engine"] == "mem0"


def test_set_engine_rejects_unknown_name():
    from agent8088 import cli
    with patch("agent8088.engine.update_simple_config") as write:
        with pytest.raises(ValueError):
            cli._memory_set_engine("pinecone")
        write.assert_not_called()


def test_engine_switch_while_memory_off_does_not_claim_unavailable(capsys):
    """With memory off no store is open, so "mem0 is unavailable" would be a lie."""
    from agent8088 import cli
    with patch("agent8088.engine.update_simple_config"), \
         patch("agent8088.engine.configure_memory"), \
         patch.dict("agent8088.engine.APP_CONFIG", {}, clear=False), \
         patch("agent8088.memory.status", return_value={"enabled": False}):
        cli.cmd_memory("engine mem0")
    out = capsys.readouterr().out.lower()
    assert "unavailable" not in out
    assert "mem0" in out


def test_engine_switch_leaves_both_stores_on_disk(tmp_path):
    """Switching engines must never delete either store's data."""
    native_db = tmp_path / "memory.db"
    native_db.write_text("sqlite")
    mem0_dir = tmp_path / "mem0"
    mem0_dir.mkdir()
    (mem0_dir / "collection").write_text("qdrant")

    from agent8088 import cli
    with patch("agent8088.engine.update_simple_config"), \
         patch("agent8088.engine.configure_memory"), \
         patch.dict("agent8088.engine.APP_CONFIG", {}, clear=False):
        cli._memory_set_engine("mem0")
        cli._memory_set_engine("native")

    assert native_db.read_text() == "sqlite"
    assert (mem0_dir / "collection").read_text() == "qdrant"


# --- agent8088 --memory-setup: install the mem0 backend post-install ---


def test_memory_setup_installs_extra_and_sets_engine(capsys):
    """--memory-setup pip-installs .[mem0], then sets memory_engine=mem0."""
    from agent8088 import cli
    with patch("agent8088.cli._install_mem0_extra", return_value=(True, "ok")) as inst, \
         patch("agent8088.memory.mem0_store._import_mem0",
               return_value=object()) as imp, \
         patch("agent8088.engine.update_simple_config") as write:
        rc = cli.run_memory_setup()
    assert rc == 0
    inst.assert_called_once()
    imp.assert_called_once()
    write.assert_called_once()
    assert write.call_args[0][1] == {"memory_engine": "mem0"}
    out = capsys.readouterr().out.lower()
    assert "mem0" in out


def test_memory_setup_import_failure_leaves_config_untouched(capsys):
    """Deps claim to install but mem0 still won't import: config must stay
    on native -- flipping it would point the user at Qdrant that cannot start."""
    from agent8088 import cli
    with patch("agent8088.cli._install_mem0_extra", return_value=(True, "ok")), \
         patch("agent8088.memory.mem0_store._import_mem0",
               return_value=None) as imp, \
         patch("agent8088.engine.update_simple_config") as write:
        rc = cli.run_memory_setup()
    assert rc == 1
    imp.assert_called_once()
    write.assert_not_called()
    out = capsys.readouterr().out.lower()
    assert "could not be imported" in out


def test_memory_setup_failure_leaves_config_untouched(capsys):
    """A failed install must not flip memory_engine -- that would point the
    user at Qdrant while the deps are still missing."""
    from agent8088 import cli
    with patch("agent8088.cli._install_mem0_extra",
                return_value=(False, "pip exploded")) as inst, \
         patch("agent8088.engine.update_simple_config") as write:
        rc = cli.run_memory_setup()
    assert rc == 1
    inst.assert_called_once()
    write.assert_not_called()
    out = capsys.readouterr().out.lower()
    assert "pip exploded" in out


def test_cli_exposes_memory_setup_flag():
    """The flag must exist in argparse, mirroring --sandbox-setup."""
    import agent8088.cli as cli_mod
    src = (Path(cli_mod.__file__).read_text(encoding="utf-8"))
    assert '"--memory-setup"' in src
    assert "args.memory_setup" in src
    assert "run_memory_setup" in src
