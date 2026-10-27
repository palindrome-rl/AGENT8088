"""A package the check's own interpreter lacks says nothing about the script.

Seen in a demo run: the agent wrote pwtest.py (it imports playwright), ran it with the interpreter that has playwright, and it
passed. The harness then started the same script with its own python, got "ModuleNotFoundError: No module named 'playwright'" and
recorded "smoke test FAILED", so the final verdict read "NOT VERIFIED ... the latest check failed" after a passing run."""
import pytest

from agent8088 import app_check
from tests.test_issue_sheet_39b import _script

OUT = "Traceback (most recent call last):\n  File \"x.py\", line 1, in <module>\nModuleNotFoundError: No module named '{}'\nCommand exited with status 1."


def test_a_missing_third_party_package_is_not_verified_rather_than_failed(tmp_path):
    script = _script(tmp_path, "import playwright\nprint('ok')\n")
    report = app_check.check_script(script, run=lambda c, t: OUT.format("playwright"))
    assert report.status == app_check.UNAVAILABLE and not report.failed
    assert "no module 'playwright'" in report.note and "not verified" in report.note
    assert report.ask


def test_a_dotted_missing_module_names_the_whole_name(tmp_path):
    script = _script(tmp_path, "import yaml.constructor\n")
    report = app_check.check_script(script, run=lambda c, t: OUT.format("yaml.constructor"))
    assert report.status == app_check.UNAVAILABLE and "'yaml.constructor'" in report.note


def test_a_missing_node_package_is_not_verified_rather_failed(tmp_path):
    script = tmp_path / "tool.js"
    script.write_text("const x = require('express');\n")
    out = "Error: Cannot find module 'express'\nRequire stack:\n- tool.js\nCommand exited with status 1."
    report = app_check.check_script(script, run=lambda c, t: out)
    if report.status == app_check.UNAVAILABLE and "node is not installed" in report.note:
        pytest.skip("node is not installed")
    assert report.status == app_check.UNAVAILABLE and "no module 'express'" in report.note


def test_an_import_of_the_scripts_own_neighbour_is_still_a_crash(tmp_path):
    (tmp_path / "helpers.py").write_text("X = 1\n")
    script = _script(tmp_path, "import helpers\n")
    report = app_check.check_script(script, run=lambda c, t: OUT.format("helpers"))
    assert report.failed and "No module named 'helpers'" in report.failures[0]


def test_a_misspelt_local_module_is_still_a_crash_only_when_it_exists_beside_the_script(tmp_path):
    (tmp_path / "pkg").mkdir()
    script = _script(tmp_path, "import pkg.sub\n")
    report = app_check.check_script(script, run=lambda c, t: OUT.format("pkg.sub"))
    assert report.failed


@pytest.mark.parametrize("out", [
    "Traceback (most recent call last):\nNameError: name 'x' is not defined\nCommand exited with status 1.",
    "Traceback (most recent call last):\nImportError: cannot import name 'y' from 'z'\nCommand exited with status 1.",
])
def test_other_crashes_still_fail(tmp_path, out):
    script = _script(tmp_path, "print(x)\n")
    assert app_check.check_script(script, run=lambda c, t: out).failed
