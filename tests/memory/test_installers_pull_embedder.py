"""Both installers must pull the embedding model, and agree on which one.

Text-level assertions, following test_cli_setup.py's
test_windows_installer_restricts_config_by_sid. install.ps1 can only be executed
on Windows and install.sh's stage would download 274 MB, so what is checkable
everywhere is that the stage exists, is wired into the run, names the same model
as the code, and does not abort the install when the pull fails.

That last property is the one worth pinning: an install that dies because a model
download failed is worse than one that says memory will use keyword search until
the model is there.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
BASH = (ROOT / "install.sh").read_text(encoding="utf-8")
POWERSHELL = (ROOT / "install.ps1").read_text(encoding="utf-8")


def _configured_embed_model():
    """The default the code actually uses, so a drift between the installers and
    memory.configure() fails here rather than at a user's first recall."""
    import re
    source = (ROOT / "src" / "agent8088" / "memory" / "__init__.py").read_text(
        encoding="utf-8")
    return re.search(r'memory_embed_model"\)\s*or\s*"([^"]+)"', source).group(1)


@pytest.mark.parametrize("installer", [BASH, POWERSHELL], ids=["install.sh", "install.ps1"])
def test_the_installer_pulls_the_embedding_model(installer):
    assert "ollama pull" in installer
    assert _configured_embed_model() in installer


@pytest.mark.parametrize("installer", [BASH, POWERSHELL], ids=["install.sh", "install.ps1"])
def test_the_installer_names_the_same_model_as_the_shipped_config(installer):
    import re
    shipped = (ROOT / "src" / "agent8088" / "config.txt").read_text(encoding="utf-8")
    configured = re.search(r'^#?\s*memory_embed_model=(.*)$', shipped,
                           re.MULTILINE).group(1).strip()
    assert configured == _configured_embed_model()
    assert configured in installer


def test_the_bash_stage_runs_as_part_of_the_install():
    assert "install_embedding_model()" in BASH
    # Declared is not enough; it has to be called from main().
    main = BASH[BASH.index("\nmain() {"):]
    assert "install_embedding_model" in main


def test_the_powershell_stage_runs_as_part_of_the_install():
    assert "function Install-Embedding-Model" in POWERSHELL
    invocation = POWERSHELL.rindex("Install-Embedding-Model")
    declaration = POWERSHELL.index("function Install-Embedding-Model")
    assert invocation > declaration, "the stage is declared but never invoked"


def test_a_failed_pull_does_not_abort_the_bash_install():
    stage = BASH[BASH.index("install_embedding_model()"):]
    stage = stage[:stage.index("\n# ---")]
    assert "exit 1" not in stage
    assert "log_warn" in stage
    assert "keyword search only" in stage


def test_a_failed_pull_does_not_abort_the_powershell_install():
    stage = POWERSHELL[POWERSHELL.index("function Install-Embedding-Model"):]
    stage = stage[:stage.index("\n# ---")]
    assert "exit 1" not in stage
    assert "Write-Warn" in stage
    assert "keyword search only" in stage


@pytest.mark.parametrize("installer", [BASH, POWERSHELL], ids=["install.sh", "install.ps1"])
def test_an_already_pulled_model_is_not_downloaded_again(installer):
    assert "already present" in installer


@pytest.mark.parametrize("installer", [BASH, POWERSHELL], ids=["install.sh", "install.ps1"])
def test_a_machine_without_ollama_is_not_treated_as_an_error(installer):
    """A cloud provider serves /embeddings itself, so there is nothing to pull."""
    assert "configured provider" in installer
