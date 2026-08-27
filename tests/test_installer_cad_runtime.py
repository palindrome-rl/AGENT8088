"""Static checks for the optional, isolated CAD installer stage."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "install.ps1").read_text(encoding="utf-8")
LINUX_INSTALLER = (ROOT / "install.sh").read_text(encoding="utf-8")


def test_windows_installer_uses_a_dedicated_runtime_not_freecad():
    assert "function Install-CadRuntime" in INSTALLER
    assert 'Join-Path $Agent8088Home "integrations\\cad"' in INSTALLER
    assert 'Join-Path $runtimeRoot "venv"' in INSTALLER
    assert "Install-FreeCAD" not in INSTALLER
    assert "FreeCAD.FreeCAD" not in INSTALLER


def test_windows_installer_pins_both_engines_and_smoke_tests_geometry():
    requirements = (ROOT / "src/agent8088/cad_runtime_requirements.txt").read_text()
    assert "build123d==0.11.1" in requirements
    assert "cadgen==0.4.28" in requirements
    assert "cad_runtime_requirements.txt" in INSTALLER
    assert '@("python", "install", "3.11")' in INSTALLER
    assert '@("venv", "--python", "3.11"' in INSTALLER
    assert "cad_worker.py" in INSTALLER
    assert "verify_cad_runtime.py" in INSTALLER
    assert "install_cad_viewer.py" in INSTALLER
    assert '"--viewer-root", $viewerRoot' in INSTALLER
    assert '@("-m", "playwright", "install", "chromium")' in INSTALLER
    assert "STEP, preview, and Viewer round-trip smoke test" in INSTALLER


def test_windows_cad_failure_is_optional_and_actionable():
    section = INSTALLER.split("function Install-CadRuntime", 1)[1].split("\nfunction ", 1)[0]
    assert "Register-SkippedStage" in section
    assert "return $false" in section
    assert "throw" not in section


def test_linux_installer_has_the_same_optional_runtime_contract():
    assert "install_cad_runtime()" in LINUX_INSTALLER
    section = LINUX_INSTALLER.split("install_cad_runtime()", 1)[1].split("\n}\n", 1)[0]
    assert '"$AGENT8088_HOME/integrations/cad"' in section
    assert '"$_root/venv"' in section
    assert "cad_runtime_requirements.txt" in section
    assert "warn_stage" in section
    assert "build123d" in section and "cadgen" in section
    assert "python install 3.11" in section
    assert "venv --python 3.11" in section
    assert '"$_py" -m playwright install chromium' in section
    assert '"$_py" -I "$_verifier"' in section
    assert '"$_py" -I "$_viewer_installer"' in section
    assert '--viewer-root "$_viewer_root"' in section
    assert "libGL.so.1" in section
    assert "libgl1" in section
    assert "libglvnd-glx" in section
    assert "libglvnd" in section
    assert "sudo -v" not in section


def test_packaging_contains_worker_requirements_renderer_and_license():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for name in (
        "cad_runtime_requirements.txt",
        "cad_snapshot_runtime/render.html",
        "cad_snapshot_runtime/snapshot-render.js",
        "cad_snapshot_runtime/TEXT_TO_CAD_LICENSE.txt",
    ):
        assert name in project


def test_viewer_installer_is_commit_and_checksum_pinned():
    installer = (ROOT / "scripts/install_cad_viewer.py").read_text(encoding="utf-8")
    assert 'VIEWER_VERSION = "0.4.28"' in installer
    assert 'VIEWER_COMMIT = "0e94cd1d2b5fa2013d89aa9504ecadcf16ce39f6"' in installer
    assert 'VIEWER_ARCHIVE_SHA256 = "8a349d4287407c79392e736c9d2e2d9c52e0427a58d168a4f325f926dfd7b7d1"' in installer
    assert 'parts[0] not in {"dist", "server_py"}' in installer
