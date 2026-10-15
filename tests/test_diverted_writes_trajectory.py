"""What the trajectory records about a write the sandbox diverted into the artifacts folder."""
from agent8088.trajectory import TrajectoryState


def test_landed_paths_name_the_place_a_diverted_write_actually_is():
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["written_paths"] = ["/w/outputs/watch.py", "/w/artifacts/notes.md"]
    state.data["redirected_writes"] = [["/w/outputs/watch.py", "/w/artifacts/outputs/watch.py"]]
    assert state.landed_paths() == ["/w/artifacts/outputs/watch.py", "/w/artifacts/notes.md"]
    assert state.written_paths() == ["/w/outputs/watch.py", "/w/artifacts/notes.md"], "the record itself is unchanged"


def test_editing_a_test_file_this_run_created_is_not_editing_an_existing_one_even_when_it_was_diverted():
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["redirected_writes"] = [["/w/test_board.py", "/w/artifacts/test_board.py"]]
    state._note_test_edit("/w/test_board.py", existed=False)         # created under the name it was asked for
    state._note_test_edit("/w/artifacts/test_board.py", existed=True)   # edited under the name it landed at
    assert state.edited_existing_tests() == []


def test_editing_a_test_file_that_was_there_before_the_run_is_still_noted():
    state = TrajectoryState({}, goal="g", message_count=1)
    state._note_test_edit("/w/tests/test_old.py", existed=True)
    assert state.edited_existing_tests() == ["/w/tests/test_old.py"]
