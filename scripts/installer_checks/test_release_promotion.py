"""Public release contracts for completion, smoke checks and sandbox packaging."""
from pathlib import Path
import importlib
import re
import io
import http.server
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_new_runtime_modules_are_shipped_and_importable():
    for name in ("completion", "app_check", "egress_proxy"):
        module = importlib.import_module(f"agent8088.{name}")
        assert Path(module.__file__).is_file()
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"src/agent8088/sandbox_python/sitecustomize.py"' in metadata
    assert (ROOT / "src/agent8088/sandbox_python/sitecustomize.py").is_file()


def test_retry_settings_remain_documented_alongside_completion_settings():
    page = (ROOT / "docs/wiki/02-configuration.md").read_text(encoding="utf-8")
    for name in ("main_llm_min_tokens", "length_retry_max_tokens",
                 "length_cutoff_max_retries", "max_completion_continuations",
                 "completion_smoke_test", "adaptive_completion_cap", "write_output_cap"):
        assert f"| `{name}` |" in page


def test_windows_setup_guidance_does_not_request_an_admin_account():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    wiki = (ROOT / "docs/wiki/06-sandboxing.md").read_text(encoding="utf-8")
    assert "normal, non-admin terminal" in readme
    assert "elevated terminal" not in readme
    assert "no admin elevation or separate sandbox account" in wiki
    assert "sandbox_backend=docker" in readme


def test_documented_install_commands_use_the_public_release_channel():
    paths = [ROOT / "README.md", *sorted((ROOT / "docs/wiki").glob("*.md"))]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        urls = re.findall(r"https://raw\.githubusercontent\.com/([^\s)]+/install\.(?:sh|ps1))", text)
        for url in urls:
            assert url == "palindrome-rl/AGENT8088/AGENT8088-v1.2/" + url.rsplit("/", 1)[1]


def test_doctor_has_a_bounded_deadline_longer_than_individual_live_probes():
    page = (ROOT / "web/src/pages/DoctorPage.tsx").read_text(encoding="utf-8")
    assert "fetchJSON<DoctorReport>('/api/doctor', 120_000)" in page
    assert "{ action: 'run diagnostics', timeoutMs }" in page


def test_recovery_and_audit_settings_are_documented_on_the_public_release():
    page = (ROOT / "docs/wiki/02-configuration.md").read_text(encoding="utf-8")
    for name in ("stream_idle_seconds", "plan_audit_confirm_fail",
                 "tool_call_format", "tool_selection_min_similarity",
                 "compaction_cooldown_turns", "compaction_summary_max_chars"):
        assert f"| `{name}` |" in page


def test_file_backup_limits_and_restore_instructions_are_documented():
    configuration = (ROOT / "docs/wiki/02-configuration.md").read_text(encoding="utf-8")
    tools = (ROOT / "docs/wiki/04-tools.md").read_text(encoding="utf-8")
    for name in ("file_backups", "file_backup_max_file_mb",
                 "file_backup_max_mb", "file_backup_keep_days"):
        assert f"| `{name}` |" in configuration
    assert "## Backups of overwritten files" in tools
    assert "index.jsonl" in tools
    assert "copy the\nbackup back over the file" in tools


def test_new_settings_preserve_native_memory_and_generic_model_examples():
    config = (ROOT / "src/agent8088/config.txt").read_text(encoding="utf-8")
    assert "\nmemory_engine=native\n" in config
    assert "# tool_selection_models=custom:your-model-name" in config
    for setting in ("stream_idle_seconds=120", "plan_audit_confirm_fail=1",
                    "tool_selection_min_similarity=0.35", "tool_call_format=native"):
        assert "# " + setting in config


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_static_smoke_server_rejects_request_bodies_without_writing(tmp_path, method):
    from agent8088.app_check import _handler
    page = tmp_path / "index.html"
    page.write_text("unchanged", encoding="utf-8")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(tmp_path, None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for _ in range(10):
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/index.html",
                data=b"not authorized to write", method=method,
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=5)
            assert error.value.code == 405
        assert page.read_text(encoding="utf-8") == "unchanged"
    finally:
        server.shutdown()
        server.server_close()


def test_rejected_request_body_drain_is_size_limited(tmp_path):
    from agent8088.app_check import _handler
    handler_type = _handler(tmp_path, None)
    handler = handler_type.__new__(handler_type)
    timeouts, statuses = [], []
    handler.headers = {"Content-Length": "1000000000"}
    handler.connection = SimpleNamespace(gettimeout=lambda: None, settimeout=timeouts.append)
    handler.rfile = io.BytesIO(b"x" * 100000)
    handler.send_error = statuses.append
    handler.do_POST()
    assert handler.rfile.tell() == 65536
    assert statuses == [405] and timeouts[-1] is None
    assert all(0 < timeout <= 0.2 for timeout in timeouts[:-1])


def test_rejected_request_body_timeout_still_returns_405(tmp_path):
    from agent8088.app_check import _handler
    handler_type = _handler(tmp_path, None)
    handler = handler_type.__new__(handler_type)
    timeouts, statuses = [], []
    handler.headers = {"Content-Length": "10"}
    handler.connection = SimpleNamespace(gettimeout=lambda: None, settimeout=timeouts.append)
    def stalled_read(size):
        raise TimeoutError("sender did not finish the request body")
    handler.rfile = SimpleNamespace(read1=stalled_read)
    handler.send_error = statuses.append
    handler.do_POST()
    assert statuses == [405] and timeouts[-1] is None
