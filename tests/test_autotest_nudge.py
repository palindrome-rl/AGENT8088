from agent8088.trajectory import TrajectoryState


def _state():
    return TrajectoryState({}, goal="change the parser", message_count=1)


def _wrote(traj, path):
    operation = traj.before_tool("write_file")
    traj.after_tool(operation, f"Wrote to {path}", failed=False, blocked=False,
                    mutated=True, path=path)


def test_a_code_write_becomes_an_untested_change():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    assert traj.needs_tests()
    assert traj.untested_paths() == ["/repo/src/parser.py"]


def test_writing_a_test_file_does_not_count_as_untested():
    traj = _state()
    _wrote(traj, "/repo/tests/test_parser.py")
    assert not traj.needs_tests()


def test_config_and_data_writes_do_not_count():
    traj = _state()
    for path in ("/repo/config.toml", "/repo/data.csv", "/repo/README.md"):
        _wrote(traj, path)
    assert not traj.needs_tests()


def test_a_failed_write_does_not_count():
    traj = _state()
    operation = traj.before_tool("write_file")
    traj.after_tool(operation, "Error: denied", failed=True, blocked=False,
                    mutated=False, path="/repo/src/parser.py")
    assert not traj.needs_tests()


def test_writing_a_test_clears_the_matching_source():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    _wrote(traj, "/repo/tests/test_parser.py")
    assert not traj.needs_tests()


def test_a_test_for_a_different_module_does_not_clear_it():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    _wrote(traj, "/repo/tests/test_lexer.py")
    assert traj.needs_tests()
    assert traj.untested_paths() == ["/repo/src/parser.py"]


def test_generate_tests_clears_everything_it_was_asked_about():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    operation = traj.before_tool("generate_tests")
    traj.after_tool(operation, "Wrote tests/test_parser.py (3 tests, all passing)",
                    failed=False, blocked=False, mutated=True, path="/repo/src/parser.py")
    assert not traj.needs_tests()


def test_a_passing_run_tests_clears_the_backlog():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    operation = traj.before_tool("run_tests")
    traj.after_tool(operation, "12 passed", failed=False, blocked=False,
                    mutated=False, verdict="passed", path="")
    assert not traj.needs_tests()


def test_the_request_fires_exactly_once():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    assert traj.request_tests() is True
    assert traj.request_tests() is False


def test_no_request_when_nothing_is_untested():
    assert _state().request_tests() is False


def test_a_new_untested_file_rearms_the_request():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    assert traj.request_tests() is True
    _wrote(traj, "/repo/src/lexer.py")
    # The previous nudge was about a different file.
    assert traj.request_tests() is True


def test_the_review_note_names_the_paths():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    traj.request_tests()
    assert "parser.py" in traj.review()


def test_the_prompt_mentions_the_backlog():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    assert "parser.py" in traj.prompt()


def test_paths_are_capped_so_state_cannot_grow_without_bound():
    traj = _state()
    for index in range(40):
        _wrote(traj, f"/repo/src/module{index}.py")
    assert len(traj.untested_paths()) <= 8


def test_old_sessions_upgrade_without_losing_state():
    # _normalise setdefaults new keys, so a live v2 session gains them rather
    # than being discarded. Bumping VERSION would throw the session away.
    old = TrajectoryState({}, goal="g", message_count=1).snapshot()
    old.pop("untested_changes", None)
    old.pop("tests_requested", None)
    revived = TrajectoryState(old, goal="g", message_count=1)
    assert revived.untested_paths() == []
    assert revived.data["goal"] == "g"
