"""A service_start refused because the service is already running is not unresolved tool work.

Seen in a test run: an earlier service_start failed with "a service named ... is already running", the model went on
and later stopped the service cleanly, and the final reply still said "Unresolved tool work remains: service_start".
The service it asked for was up, so nothing was left undone. Any other failed start still counts.
"""
from agent8088.trajectory import TrajectoryState


def _call(state, tool, result, failed):
    op = state.before_tool(tool)
    state.after_tool(op, result, failed=failed, blocked=False, mutated=False)


def test_already_running_is_not_unresolved():
    state = TrajectoryState({}, goal="Serve the page and test it.", message_count=1)
    _call(state, "service_start", "Started web at http://127.0.0.1:8001", False)
    _call(state, "service_start", "Error: a service named web is already running (http://127.0.0.1:8001); stop it "
                                  "first, or give the new one another name", True)
    _call(state, "service_stop", "Stopped web.", False)
    assert "service_start" not in state.unresolved_tools()


def test_a_start_that_could_not_start_stays_unresolved():
    state = TrajectoryState({}, goal="Serve the page and test it.", message_count=1)
    _call(state, "service_start", "Error: the service could not start: port in use", True)
    _call(state, "service_stop", "Error: no service named 'web' is running. service_status lists them.", True)
    assert "service_start" in state.unresolved_tools()
