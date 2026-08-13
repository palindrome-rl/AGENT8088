"""requirements.txt must declare the same packages as [project.dependencies].

pyproject.toml is the source of truth, so requirements.txt is hand-maintained and
nothing enforced that the two agreed. It drifted twice; the second time it listed
three packages while the engine needed five, and since `mcp` and `ddgs` are both
imported lazily the symptom was not an ImportError but a quietly reduced agent.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Loaded without registering in sys.modules: the script has no self-imports, and
# leaving an entry behind is global state that outlives this file's tests.
_spec = importlib.util.spec_from_file_location(
    "check_requirements_sync", ROOT / "scripts" / "check_requirements_sync.py")
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def test_the_shipped_files_agree():
    """The check that actually guards the repo."""
    assert check.main([]) == 0


def test_a_missing_dependency_is_caught(tmp_path, capsys):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        'dependencies = [\n    "openai>=1.0.0,<3",\n    "mcp>=1.27,<2",\n]\n',
        encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("openai>=1.0.0\n", encoding="utf-8")

    assert check.main([str(pyproject), str(requirements)]) == 1
    assert "missing from requirements.txt: mcp" in capsys.readouterr().out


def test_an_extra_dependency_is_caught(tmp_path, capsys):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('dependencies = [\n    "openai>=1.0.0",\n]\n', encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("openai>=1.0.0\nleftover-package>=2\n", encoding="utf-8")

    assert check.main([str(pyproject), str(requirements)]) == 1
    assert "not a declared dependency:     leftover-package" in capsys.readouterr().out


def test_version_pins_may_differ_between_the_two_files(tmp_path):
    """Names are compared, not specifiers — the files legitimately pin differently,
    and a check that failed on that would just get switched off."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('dependencies = [\n    "openai>=1.0.0,<3",\n]\n', encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("openai>=2.0\n", encoding="utf-8")

    assert check.main([str(pyproject), str(requirements)]) == 0


def test_casing_and_separators_are_normalised(tmp_path):
    """InquirerPy vs inquirerpy, discord.py vs discord-py: PEP 503 says equal."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        'dependencies = [\n    "InquirerPy>=0.3.4",\n    "discord.py>=2.3.0",\n]\n',
        encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("inquirerpy>=0.3.4\ndiscord-py>=2.3.0\n", encoding="utf-8")

    assert check.main([str(pyproject), str(requirements)]) == 0


def test_comments_blank_lines_and_flags_are_ignored(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('dependencies = [\n    "openai>=1.0.0",\n]\n', encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        "# a comment\n\n-r other.txt\n--index-url https://example.com\n"
        "openai>=1.0.0  # trailing comment\n", encoding="utf-8")

    assert check.main([str(pyproject), str(requirements)]) == 0


def test_extras_markers_and_environment_conditions_parse(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        'dependencies = [\n    "httpx[brotli]>=0.24.0",\n'
        '    "tomli>=2.0; python_version < \'3.11\'",\n]\n', encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        "httpx[brotli]>=0.24.0\ntomli>=2.0; python_version < '3.11'\n", encoding="utf-8")

    assert check.main([str(pyproject), str(requirements)]) == 0


def test_the_release_gate_runs_this_check():
    """Guards the wiring: the check is worthless if nothing invokes it."""
    gate = (ROOT / "scripts" / "release_check.py").read_text(encoding="utf-8")
    assert "check_requirements_sync.py" in gate
