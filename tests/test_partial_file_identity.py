from agent8088.trajectory import TrajectoryState


def test_same_basename_in_other_directory_cannot_complete_partial_file():
    state = TrajectoryState(None)
    state.note_partial("a/index.js", 1200)
    operation = state.before_tool("write_file")
    state.after_tool(operation, "Wrote bytes", failed=False, blocked=False,
        mutated=True, path="b/index.js")
    assert state.partial_files() == ["a/index.js"]


def test_each_directory_can_salvage_same_basename_once():
    state = TrajectoryState(None)
    state.mark_salvaged("a/index.js")
    assert state.salvaged_before("a/./index.js")
    assert not state.salvaged_before("b/index.js")
    state.note_partial("a/index.js", 1200)
    state.note_partial("b/index.js", 1300)
    assert state.partial_files() == ["a/index.js", "b/index.js"]


def test_resolved_path_continuation_clears_correct_marker(tmp_path):
    state = TrajectoryState(None)
    path = str(tmp_path / "a" / "index.js")
    state.note_partial(path, 1200)
    state.after_tool(state.before_tool("write_file"), "Appended bytes",
        failed=False, blocked=False, mutated=True, path="a/index.js", partial_path=path)
    assert state.partial_files() == []


def test_windows_path_separator_aliases_are_same_file():
    state = TrajectoryState(None)
    state.mark_salvaged(r"C:\work\a\index.js")
    assert state.salvaged_before("c:/work/a/index.js")
    assert not state.salvaged_before("c:/work/b/index.js")


def test_failed_write_does_not_clear_marker():
    state = TrajectoryState(None)
    state.note_partial("a/index.js", 1200)
    state.after_tool(state.before_tool("write_file"), "Error: blocked",
        failed=True, blocked=True, mutated=False, path="a/index.js")
    assert state.partial_files() == ["a/index.js"]
