from pathlib import Path

from agent8088 import web_server


def test_vite_uses_the_selected_backend_port(monkeypatch, tmp_path: Path):
    (tmp_path / "node_modules").mkdir()
    started = {}

    class Process:
        pass

    monkeypatch.setattr(web_server.shutil, "which", lambda _: "/usr/bin/npm")
    monkeypatch.setattr(web_server.subprocess, "Popen", lambda *args, **kwargs: started.update(args=args, kwargs=kwargs) or Process())

    web_server._start_vite(tmp_path, 9123, "127.0.0.1")

    assert started["args"][0][-3:] == ["--port", "5180", "--strictPort"]
    assert started["kwargs"]["env"]["AGENT8088_WEB_BACKEND"] == "http://127.0.0.1:9123"
