"""The given tests are copied once, before the run changes anything, so a check weakened by any tool counts.

The rule against weakening a given test first saw only write_file and edit_file. A model that changes a test with
`sed -i`, `echo >` or `mv`, or deletes it, went unseen. The copy is taken on the run's first tool call, before any
change, and the verdict compares it with what is on disk. A test the run adds is not in the copy.
"""
from agent8088.trajectory import TrajectoryState

GIVEN = "def test_body():\n    assert to_fahrenheit(37) == 98.0\n"


def _project(tmp_path):
    (tmp_path / "test_temperature.py").write_text(GIVEN, encoding="utf-8")
    (tmp_path / "temperature.py").write_text("def to_fahrenheit(c):\n    return c * 9 / 5 + 32\n", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "test_vendor.py").write_text("assert 1\n", encoding="utf-8")
    return tmp_path


def _state(engine, tmp_path, goal="All tests must pass. Don't change the tests."):
    state = TrajectoryState({}, goal=goal, message_count=1, workspace=str(tmp_path))
    engine._snapshot_given_tests(state, tmp_path)
    return state


def test_a_shell_edit_of_a_given_test_counts(engine, tmp_path):
    state = _state(engine, _project(tmp_path))
    (tmp_path / "test_temperature.py").write_text(GIVEN.replace("98.0", "98.6"), encoding="utf-8")
    assert state.weakened_tests() == ["test_temperature.py"]


def test_deleting_a_given_test_counts(engine, tmp_path):
    state = _state(engine, _project(tmp_path))
    (tmp_path / "test_temperature.py").unlink()
    assert state.weakened_tests() == ["test_temperature.py"]


def test_an_unchanged_or_extended_test_does_not(engine, tmp_path):
    state = _state(engine, _project(tmp_path))
    assert state.weakened_tests() == []
    (tmp_path / "test_temperature.py").write_text(GIVEN + "\ndef test_freezing():\n    assert 1\n", encoding="utf-8")
    assert state.weakened_tests() == []


def test_a_test_the_run_adds_is_not_in_the_copy(engine, tmp_path):
    state = _state(engine, _project(tmp_path))
    (tmp_path / "test_new.py").write_text("def test_x():\n    assert 1\n", encoding="utf-8")
    (tmp_path / "test_new.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    assert state.weakened_tests() == []


def test_vendored_folders_are_not_copied(engine, tmp_path):
    state = _state(engine, _project(tmp_path))
    assert all("node_modules" not in v["path"] for v in state.data["test_originals"].values())


def test_the_copy_is_taken_once(engine, tmp_path):
    state = _state(engine, _project(tmp_path))
    (tmp_path / "test_temperature.py").write_text(GIVEN.replace("98.0", "98.6"), encoding="utf-8")
    engine._snapshot_given_tests(state, tmp_path)   # a later call keeps the original
    assert state.weakened_tests() == ["test_temperature.py"]


def test_a_request_to_change_the_tests_is_exempt(engine, tmp_path):
    state = _state(engine, _project(tmp_path), goal="The body temperature test is wrong; update the test to 98.6.")
    (tmp_path / "test_temperature.py").unlink()
    assert state.weakened_tests() == []
