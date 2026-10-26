"""A page that calls its own server or a local service (Ollama, a local API) passes the page check without that call ever being
tried, and the report said only "smoke test passed (5 warnings)". The summary now counts the calls it could not reach, the
warning for the page's own server says the call was NOT verified and how to verify it, and the final answer carries the note
for either kind of call."""
import pytest

from agent8088 import app_check
from agent8088.trajectory import TrajectoryState

SERVER_WARNING = ("the page asked the server for /api/pokemon (GET, 404); this check serves static files only, so the backend "
                  "was not running and that call was NOT verified.")
LOCAL_WARNING = ("the page calls a local service (http://localhost:11434/v1/systemone, POST); this check blocks it, so that call "
                 "was NOT verified.")


def _report(*warnings):
    return app_check.Report("web", "E:/w/index.html", app_check.PASS, warnings=list(warnings))


def test_summary_names_the_calls_it_could_not_reach():
    text = _report(SERVER_WARNING, LOCAL_WARNING, "no visible button").summary()
    assert text == "index.html: smoke test passed (3 warnings; 2 calls to a service it could not reach, not verified)"


def test_one_call_is_singular():
    assert "1 call to a service it could not reach" in _report(SERVER_WARNING).summary()


def test_summary_is_unchanged_without_such_calls():
    assert _report("no visible button").summary() == "index.html: smoke test passed (1 warning)"
    assert _report().summary() == "index.html: smoke test passed"


@pytest.mark.parametrize("warning", [SERVER_WARNING, LOCAL_WARNING])
def test_both_kinds_of_call_reach_the_final_answer(engine, warning):
    state = TrajectoryState({}, goal="Create a web app", message_count=1)
    state.data.update({"mutation_count": 1, "ran_ok_at": 1, "written_paths": ["index.html"]})
    state.record_smoke("E:/w/index.html", _report(warning).summary(), False, passed=True)
    if "asked the server for" in warning or "calls a local service" in warning:
        state.note_backend_asked("E:/w/index.html")
    answer = engine._with_task_notes("Done.", state)
    assert "Not verified: the page calls its own server or a local service" in answer


def test_the_server_warning_keeps_the_phrase_the_answer_note_keys_on(tmp_path):
    source = (app_check.__file__)
    text = open(source, encoding="utf-8").read()
    assert "asked the server for" in text and "that call was NOT" in text and "shell command" in text
