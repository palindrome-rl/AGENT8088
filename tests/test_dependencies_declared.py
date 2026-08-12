"""Every third-party package imported by src/ is declared in pyproject.toml.

Relying on a transitive dependency works right up until an upstream drops or
re-bounds it. Here that failure would have been silent rather than loud, because
both call sites catch ImportError: Tab completion would have disappeared and
every syntax theme would have validated as invalid, with no error either way.
prompt_toolkit (via InquirerPy) and Pygments (via rich) were both in that state.
"""
import ast
import sys
from pathlib import Path

import pytest

tomllib = pytest.importorskip(
    "tomllib", reason="tomllib is stdlib on 3.11+; this check needs a TOML parser")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Import name -> the distribution that provides it, where the two differ.
IMPORT_TO_DIST = {
    "PIL": "pillow",
    "discord": "discord.py",
    "slack_bolt": "slack-bolt",
    "slack_sdk": "slack-sdk",
    "telegram": "python-telegram-bot",
    "prompt_toolkit": "prompt_toolkit",
    "pygments": "pygments",
}

# Imported behind a try/except that raises an actionable install message, and
# deliberately outside the base install.
OPTIONAL_BY_DESIGN = {"litellm"}


def _declared_distributions() -> set:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    specs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specs.extend(extra)
    names = set()
    for spec in specs:
        name = spec.split(";")[0].strip()
        for sep in ("[", ">", "<", "=", "!", "~", " "):
            name = name.split(sep)[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


def _imported_third_party() -> dict:
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is a relative import — always first-party.
                names = ([node.module.split(".")[0]]
                         if node.module and node.level == 0 else [])
            else:
                continue
            for name in names:
                if name and name not in sys.stdlib_module_names and name != "agent8088":
                    found.setdefault(name, set()).add(
                        str(path.relative_to(ROOT)))
    return found


def test_every_imported_package_is_declared():
    declared = _declared_distributions()
    undeclared = {}
    for module, files in _imported_third_party().items():
        if module in OPTIONAL_BY_DESIGN:
            continue
        dist = IMPORT_TO_DIST.get(module, module).lower().replace("_", "-")
        if dist not in declared:
            undeclared[module] = sorted(files)

    assert not undeclared, (
        "imported but not declared in pyproject.toml — these reach the install "
        f"only as transitive dependencies: {undeclared}")


def test_the_two_that_were_transitive_are_now_pinned():
    """Regression guard: both were reaching the install only via rich/InquirerPy."""
    declared = _declared_distributions()
    assert "prompt-toolkit" in declared
    assert "pygments" in declared


def test_litellm_is_installable_as_an_extra():
    """engine.py tells the user to `pip install litellm`; an extra makes that a
    supported path rather than a package name to guess."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert any("litellm" in spec for spec in extras.get("litellm", []))


def test_requirements_txt_lists_every_base_dependency():
    """requirements.txt is hand-kept and has drifted before, quietly reducing the
    agent rather than erroring."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    listed = {
        line.split(">")[0].split("<")[0].split("=")[0].strip().lower()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for spec in data["project"]["dependencies"]:
        name = spec.split(">")[0].split("<")[0].split("=")[0].strip().lower()
        assert name in listed, f"{name} is in pyproject but missing from requirements.txt"
