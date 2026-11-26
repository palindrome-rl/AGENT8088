"""Work is not VERIFIED when it changed or skipped a check in a test that came with the task.

Seen in a benchmark run: the given tests contradicted each other, and the model changed the expected value in one
(98.0 to 98.6) so all four passed, then reported "Done, all four tests now pass" with a VERIFIED verdict. The run
only added a test-integrity note. Adding tests to an existing file is fine, and so is changing a test the request
asked to change.
"""
from agent8088 import engine
from agent8088.trajectory import TrajectoryState

ORIGINAL = (
    "from temperature import to_fahrenheit\n\n"
    "def test_freezing():\n    assert to_fahrenheit(0) == 32\n\n"
    "def test_body():\n    assert to_fahrenheit(37) == 98.0\n")


def _edited(tmp_path, new_text, goal="Make the failing test pass."):
    test = tmp_path / "test_temperature.py"
    test.write_text(ORIGINAL, encoding="utf-8")
    state = TrajectoryState({}, goal=goal, message_count=1)
    state.remember_test_original(str(test), ORIGINAL)
    op = state.before_tool("edit_file")
    test.write_text(new_text, encoding="utf-8")
    state.after_tool(op, "Updated", failed=False, blocked=False, mutated=True, path=str(test), existed=True)
    return state


def test_a_changed_expected_value_is_a_weakened_test(tmp_path):
    state = _edited(tmp_path, ORIGINAL.replace("== 98.0", "== 98.6"))
    assert state.weakened_tests() == ["test_temperature.py"]
    assert any("test_temperature.py" in r for r in engine._not_verified_reasons(state, "Done, all tests pass."))


def test_a_skip_marker_is_a_weakened_test(tmp_path):
    state = _edited(tmp_path, "import pytest\n" + ORIGINAL.replace("def test_body", "@pytest.mark.skip\ndef test_body"))
    assert state.weakened_tests() == ["test_temperature.py"]


def test_an_added_test_is_not(tmp_path):
    state = _edited(tmp_path, ORIGINAL + "\ndef test_boiling():\n    assert to_fahrenheit(100) == 212\n")
    assert state.weakened_tests() == []
    assert not any("test" in r and "changed" in r for r in engine._not_verified_reasons(state, "Done."))


def test_a_test_change_the_request_asked_for_is_not(tmp_path):
    state = _edited(tmp_path, ORIGINAL.replace("== 98.0", "== 98.6"),
                    goal="The body temperature test is wrong; update the test to 98.6.")
    assert state.weakened_tests() == []


def test_without_the_original_nothing_is_claimed(tmp_path):
    test = tmp_path / "test_x.py"
    test.write_text("def test_a():\n    assert 1\n", encoding="utf-8")
    state = TrajectoryState({}, goal="x", message_count=1)
    op = state.before_tool("edit_file")
    state.after_tool(op, "Updated", failed=False, blocked=False, mutated=True, path=str(test), existed=True)
    assert state.weakened_tests() == []


def test_only_test_files_are_remembered(tmp_path):
    state = TrajectoryState({}, goal="x", message_count=1)
    state.remember_test_original(str(tmp_path / "temperature.py"), "x = 1\n")
    assert not state.data.get("test_originals")


def test_only_a_request_to_change_the_tests_is_exempt():
    from agent8088.trajectory import _asks_to_change_tests
    assert _asks_to_change_tests("The body temperature test is wrong; update the test to 98.6.")
    assert _asks_to_change_tests("Fix the body temperature test.")
    assert not _asks_to_change_tests("All four tests must pass. Don't change the tests.")
    assert not _asks_to_change_tests("Please fix shipping.py. Do not change the tests that are already there.")
    assert not _asks_to_change_tests("If it does, fix it and add a test for it.")
