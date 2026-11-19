"""A config.txt typo must degrade to a default plus a warning, never a traceback.

`context_window=32k` used to raise ValueError while `import agent8088.engine`
was still running, which killed --setup and --version too -- the two commands
a person would use to fix it.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parent.parent / "src")


def test_config_number_falls_back_and_warns(engine):
    engine.CONFIG_WARNINGS.clear()
    cfg = {"context_window": "32k", "ratio": "0.5", "steps": "12", "big": "1e3", "blank": ""}
    assert engine._config_number("context_window", 32768, int, cfg) == 32768
    assert engine._config_number("ratio", 0.1, float, cfg) == 0.5
    assert engine._config_number("steps", 3, int, cfg) == 12
    assert engine._config_number("big", 3, int, cfg) == 1000
    assert engine._config_number("blank", 7, int, cfg) == 7
    assert engine._config_number("missing", 9, int, cfg) == 9
    assert engine.CONFIG_WARNINGS == [
        "config.txt: context_window='32k' is not a number, using 32768."]


def test_warnings_are_not_repeated(engine):
    engine.CONFIG_WARNINGS.clear()
    for _ in range(3):
        engine._config_number("x", 1, int, {"x": "nope"})
    assert len(engine.CONFIG_WARNINGS) == 1


def test_unreadable_config_warns_and_uses_defaults(engine, tmp_path):
    path = tmp_path / "config.txt"
    path.write_bytes(b"model_name=\xff\xfe\xfa broken")  # invalid UTF-8, no BOM
    engine.CONFIG_WARNINGS.clear()
    assert engine.load_simple_config(path) == {}
    assert "not valid UTF-8" in engine.CONFIG_WARNINGS[0]
    assert "agent8088 --setup" in engine.CONFIG_WARNINGS[0]


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="POSIX permission bits; root reads anything")
def test_permission_denied_config_warns(engine, tmp_path):
    path = tmp_path / "config.txt"
    path.write_text("model_name=x\n", encoding="utf-8")
    path.chmod(0)
    try:
        engine.CONFIG_WARNINGS.clear()
        assert engine.load_simple_config(path) == {}
        assert str(path) in engine.CONFIG_WARNINGS[0]
        assert "permissions" in engine.CONFIG_WARNINGS[0]
    finally:
        path.chmod(0o600)


def test_utf16_config_with_bom_is_read(engine, tmp_path):
    path = tmp_path / "config.txt"
    path.write_bytes("model_name=llama3\ncontext_window=8192\n".encode("utf-16"))
    assert engine.load_simple_config(path) == {"model_name": "llama3", "context_window": "8192"}


def test_provider_without_base_url_is_reported(engine):
    engine.CONFIG_WARNINGS.clear()
    providers = engine.load_providers({"provider.mybox.model": "m"})
    assert "mybox" not in providers
    assert any("provider 'mybox' has no base_url" in w for w in engine.CONFIG_WARNINGS)


def test_builtin_provider_needs_no_base_url(engine):
    engine.CONFIG_WARNINGS.clear()
    providers = engine.load_providers({"provider.groq.model": "m"})
    assert providers["groq"]["base_url"].startswith("https://")
    assert engine.CONFIG_WARNINGS == []


def test_active_endpoint_url_follows_the_active_provider(engine, monkeypatch):
    monkeypatch.setitem(engine.PROVIDERS, "box", {"base_url": "http://box:9000/v1", "model": "m"})
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "box")
    assert engine.active_endpoint_url() == "http://box:9000/v1"
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "")
    assert engine.active_endpoint_url() == engine.MODEL_BASE_URL


def _import_with(tmp_path, config_text, script):
    config = tmp_path / "config.txt"
    config.write_text(config_text, encoding="utf-8")
    env = dict(os.environ, HOME=str(tmp_path), AGENT8088_HOME=str(tmp_path),
               AGENT8088_CONFIG=str(config), AGENT8088_SANDBOX="local",
               PYTHONPATH=SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.pop("AGENT8088_PROVIDER", None)
    return subprocess.run([sys.executable, "-c", textwrap.dedent(script)],
                          capture_output=True, text=True, timeout=180, env=env, cwd=str(tmp_path))


def test_import_survives_typos_and_lists_them(tmp_path):
    result = _import_with(tmp_path, (
        "context_window=32k\n"
        "api_retry_jitter_ratio=lots\n"
        "default_provider=olama\n"
        "max_turnz=3\n"
        # A dead, never-routable endpoint so nothing real is contacted.
        "model_base_url=http://192.0.2.1:9/v1\n"
    ), """
        import agent8088.engine as A
        print(A.CONTEXT_WINDOW, A.API_RETRY_JITTER_RATIO)
        print("\\n".join(A.CONFIG_WARNINGS))
        print("ENDPOINT", A.ACTIVE_ENDPOINT_URL)
    """)
    assert result.returncode == 0, result.stderr[-1500:]
    out = result.stdout
    assert "32768 0.1" in out.splitlines()
    assert "context_window='32k' is not a number" in out
    assert "default_provider='olama'" in out and "did you mean 'ollama'" in out
    assert "unknown setting 'max_turnz'" in out and "'max_turns'" in out
    assert "ENDPOINT http://192.0.2.1:9/v1" in out
    # Warnings are for the banner, not dumped on stderr at import.
    assert "not a number" not in result.stderr
