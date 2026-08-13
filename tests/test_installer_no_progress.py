from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_windows_installer_does_not_own_a_custom_progress_renderer():
    script = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "Invoke-WithProgress" not in script
    assert "Format-ProgressBar" not in script


def test_installers_update_from_the_selected_branch():
    windows = (ROOT / "install.ps1").read_text(encoding="utf-8")
    unix = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "AGENT8088/$Branch/install.ps1" in windows
    assert "AGENT8088/$BRANCH/install.sh" in unix


def test_installers_persist_the_selected_workspace_as_project_root():
    windows = (ROOT / "install.ps1").read_text(encoding="utf-8")
    unix = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert '"project_root=$projectRoot"' in windows
    assert 'echo "project_root=$project_root"' in unix
