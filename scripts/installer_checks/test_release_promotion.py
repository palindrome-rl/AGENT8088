"""Public release contracts for completion, smoke checks and sandbox packaging."""
from pathlib import Path
import importlib
import re


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
