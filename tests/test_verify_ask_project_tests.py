"""The "run what you changed" reminder names the project's own tests when they are near the changed files, and never
tells a model to run a package's module files one by one as scripts (their relative imports fail, which is a problem
with the check and made one run rewrite working imports)."""
from agent8088 import completion


def _project(tmp_path, runner="unittest"):
    root = tmp_path / "work" / "proj"
    (root / "booking").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "booking" / "__init__.py").write_text("")
    (root / "booking" / "rooms.py").write_text("x = 1\n")
    (root / "tests" / "test_rooms.py").write_text(f"import {runner}\n")
    return root


def test_tests_beside_the_package_are_named_with_their_folder(tmp_path):
    root = _project(tmp_path)
    ask = completion.verify_ask([str(root / "booking" / "rooms.py")], False)
    assert f"`python -m unittest discover -s tests` from `{root}`" in ask
    assert "python rooms.py" not in ask


def test_a_pytest_suite_is_run_with_pytest(tmp_path):
    root = _project(tmp_path, runner="pytest")
    ask = completion.verify_ask([str(root / "booking" / "rooms.py")], False)
    assert "`python -m pytest -q`" in ask


def test_a_package_without_tests_is_imported_not_run_as_a_script(tmp_path):
    root = tmp_path / "work" / "lib" / "pkg"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "mod.py").write_text("x = 1\n")
    ask = completion.verify_ask([str(root / "mod.py")], False)
    assert '`python -c "import pkg.mod"`' in ask and str(root.parent) in ask


def test_a_plain_script_is_still_run_directly(tmp_path):
    folder = tmp_path / "work" / "tool"
    folder.mkdir(parents=True)
    (folder / "app.py").write_text("print(1)\n")
    assert "`python app.py`" in completion.verify_ask([str(folder / "app.py")], False)


def test_the_known_project_tests_still_win(tmp_path):
    root = _project(tmp_path)
    assert "`run_tests`" in completion.verify_ask([str(root / "booking" / "rooms.py")], True)


def test_a_file_far_from_any_project_finds_no_suite(tmp_path):
    folder = tmp_path / "a"
    folder.mkdir()
    (folder / "app.py").write_text("print(1)\n")
    assert completion.tests_near([str(folder / "app.py")]) is None


def test_a_suite_in_a_javascript_project_is_npm_test(tmp_path):
    root = tmp_path / "web"
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text('{"scripts": {"test": "node test.js"}}')
    (root / "src" / "a.js").write_text("1")
    found = completion.tests_near([str(root / "src" / "a.js")])
    assert found and found[1] == "npm test"


def test_a_relative_path_says_nothing_about_the_project(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("import unittest\n")
    monkeypatch.chdir(tmp_path)
    assert completion.tests_near(["app.py"]) is None
    assert "`python app.py`" in completion.verify_ask(["app.py"], False)
