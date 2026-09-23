"""AGENT8088_DISPOSABLE_CONTAINER=1: behaviour for throwaway evaluation containers.

Every test here reloads the engine with the flag explicitly on or off, so the
flag-off cases double as regression guards for normal installs.
"""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _reload(monkeypatch, on, **config):
    cfg = ROOT / "_no_such_config.txt"
    if config:
        cfg = Path(config.pop("_tmp")) / "config.txt"
        cfg.write_text("".join(f"{k}={v}\n" for k, v in config.items()), encoding="utf-8")
    monkeypatch.setenv("AGENT8088_CONFIG", str(cfg))
    monkeypatch.setenv("AGENT8088_SANDBOX", "local")
    if on:
        monkeypatch.setenv("AGENT8088_DISPOSABLE_CONTAINER", "1")
    else:
        monkeypatch.delenv("AGENT8088_DISPOSABLE_CONTAINER", raising=False)
    sys.path.insert(0, str(ROOT / "src"))
    from agent8088 import engine
    return importlib.reload(engine)


def test_flag_off_by_default(monkeypatch):
    e = _reload(monkeypatch, on=False)
    assert e.DISPOSABLE_CONTAINER is False
    assert e.ARTIFACTS_ROOT == (e.PROJECT_ROOT / "artifacts").resolve()


def test_flag_moves_artifacts_out_of_project(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    e = _reload(monkeypatch, on=True)
    assert e.DISPOSABLE_CONTAINER is True
    assert e.PROJECT_ROOT not in e.ARTIFACTS_ROOT.parents
    assert e.ARTIFACTS_ROOT == (tmp_path / "home" / "artifacts").resolve()
