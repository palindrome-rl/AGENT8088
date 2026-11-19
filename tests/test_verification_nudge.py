from agent8088.trajectory import TrajectoryState


def _state(goal="build the parser"):
    return TrajectoryState({}, goal=goal, message_count=1)


def _wrote(traj, path):
    operation = traj.before_tool("write_file")
    traj.after_tool(operation, f"Wrote to {path}", failed=False, blocked=False,
                    mutated=True, path=path)


def _ran(traj, command, result="ok", verdict="unknown"):
    operation = traj.before_tool("execute_shell")
    traj.after_tool(operation, result, failed=False, blocked=False,
                    mutated=False, verdict=verdict, command=command)


def test_scratch_only_changes_get_no_nudge():
    traj = _state()
    for path in ("/repo/scratch/cruft.py", "/repo/tmp/x.py", "/repo/scrape_prices.py",
                 "/repo/inspect_db.py", "/repo/download_files.py", "/repo/debug_sql.py"):
        _wrote(traj, path)
    assert not traj.needs_tests()
    assert traj.request_tests() is False


def test_scratch_files_inside_the_workspace_are_judged_relatively():
    # A project living under a folder called 'temp' must not make its own
    # files look like scratch.
    traj = TrajectoryState({}, goal="build the parser", message_count=1,
                           workspace="C:/work/temp/proj")
    _wrote(traj, "C:/work/temp/proj/src/parser.py")
    assert traj.untested_paths() == ["C:/work/temp/proj/src/parser.py"]


def test_deliverable_changed_and_never_run_is_nudged_to_run():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    assert traj.request_tests() is True
    assert "run" in traj.nudge_text().lower()
    assert "parser.py" in traj.nudge_text()


def test_running_the_deliverable_afterwards_clears_the_need():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    _ran(traj, "python src/parser.py")
    assert not traj.needs_tests()
    assert traj.request_tests() is False


def test_a_failed_run_does_not_clear_the_need():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    _ran(traj, "python src/parser.py", result="Traceback...\nCommand exited with status 1")
    assert traj.needs_tests()


def test_a_run_of_a_different_file_does_not_clear_it():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    _ran(traj, "python src/lexer.py")
    assert traj.untested_paths() == ["/repo/src/parser.py"]


def test_running_the_module_name_also_counts():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    _ran(traj, "python -m parser --help")
    assert not traj.needs_tests()


def test_a_new_change_after_running_rearms_the_nudge():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    assert traj.request_tests() is True
    _ran(traj, "python src/parser.py")
    _wrote(traj, "/repo/src/parser.py")
    assert traj.request_tests() is True


def test_a_new_untested_file_rearms_after_the_one_shot_fired():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    assert traj.request_tests() is True
    _wrote(traj, "/repo/src/lexer.py")
    assert traj.request_tests() is True


def test_nudge_never_invites_finishing():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    text = traj.nudge_text().lower()
    for phrase in ("final", "finish", "done", "complete", "before you", "why",
                   "not apply", "answer", "wrap up"):
        assert phrase not in text, phrase


def test_nudge_asks_for_tests_only_when_the_user_asked_for_them():
    plain = _state("fix the parser")
    _wrote(plain, "/repo/src/parser.py")
    assert "generate_tests" not in plain.nudge_text()

    asked = _state("write parser tests")
    _wrote(asked, "/repo/src/parser.py")
    assert "generate_tests" in asked.nudge_text()


def test_a_passing_run_tests_still_clears_everything():
    traj = _state()
    _wrote(traj, "/repo/src/parser.py")
    operation = traj.before_tool("run_tests")
    traj.after_tool(operation, "12 passed", failed=False, blocked=False,
                    mutated=False, verdict="passed", path="")
    assert not traj.needs_tests()
