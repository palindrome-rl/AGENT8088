"""The credential scan no longer reports what saved web pages and ordinary scripts are full of, and the end-of-run notes say once that no test run passed.

Seen in real runs: the scan flagged `?v=c0e0ed65...` cache-busters and percent-encoded font links in saved reference pages, and a
`User-Agent: agent8088-pokemon-demo/1.0` in the run's own fetch script. Each flag made the model edit downloaded documents or
rewrite files, and cost one run its last ten turns. Real keys, including a hex key behind `token=` or `api_key=`, are still reported."""
import pytest

from agent8088 import secret_scan as ss
from agent8088.trajectory import TrajectoryState


def _masked(text):
    return [f.masked for f in ss.find_secrets(text)]


@pytest.mark.parametrize("text", [
    '<link rel="stylesheet" href="/static/app.css?v=c0e0ed650146">',
    '<script src="/static/app.js?v=c0e0ed65d7a4b3f1e2a9c8b7a6d5e4f30146"></script>',
    '<link href="//fonts.googleapis.com/css?family=Open+Sans%7CRoboto%7CMontserrat:400,700&subset=latin" rel="stylesheet">',
    'headers={"User-Agent": "agent8088-pokemon-demo/1.0"}',
    'req = Request(url, headers={"User-Agent": "Pokedex2Tool/2.1"})',
    'app.js?ver=9f86d081884c7d65 and style.css?build=a1b2c3d4e5f6',
])
def test_page_and_script_noise_is_not_a_credential(text):
    assert _masked(text) == []


@pytest.mark.parametrize("text", [
    "api_key=9f86d081884c7d659a2feaa0c55ad015",
    "token=c0e0ed65d7a4b3f1e2a9c8b7a6d5e4f30146",
    "secret = Xk9fQ2mZp7LwR4tYb8NcV3hJd6sA1eUg",
    "key sk-proj-abcdefghijklmnopqrstuvwx1234567890",
    "v=Xk9fQ2mZp7LwR4tYb8NcV3hJd6sA1eUg",                    # only hex is excused behind v=
    "agent=Xk9fQ2mZp7LwR4tYb8NcV3hJd6sA1eUg/1.0",
    "%7Cpassword=Xk9fQ2mZp7LwR4tYb8NcV3hJd6sA1eUg",           # an escape in front does not hide the key
])
def test_real_looking_keys_are_still_reported(text):
    assert _masked(text)


def _changed_state(inspected=True):
    state = TrajectoryState({}, goal="g", message_count=1)
    operation = state.before_tool("write_file")
    state.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, verdict="unknown", path="C:\\w\\a.js")
    state.data["verification"] = "inspected" if inspected else "pending"
    state.data["ran_ok_at"] = state.mutation_count()           # a program the run wrote ran without an error: "exercised"
    return state


def test_the_no_test_passed_sentence_is_said_once(engine):
    state = _changed_state()
    assert state.assurance() == "exercised"
    answer = engine._with_task_notes("Built it.", state)
    assert answer.count("no test run passed") == 1
    assert "no automated verification passed" not in answer


def test_other_task_state_text_is_kept(engine):
    state = _changed_state()
    state.data["unmet_checks"] = ["a part of the request was not done"]
    answer = engine._with_task_notes("Built it.", state)
    assert "Task-state note: " in answer and "Not completed: a part of the request was not done." in answer
    assert "no automated verification passed" not in answer


def test_without_an_assurance_line_the_sentence_stays(engine):
    state = _changed_state()
    state.data.pop("ran_ok_at")
    state.data["smoke_passed_at"] = {}
    assert state.assurance() == "inspected"
    assert "no automated verification passed" in engine._with_task_notes("Built it.", state)


@pytest.mark.parametrize('text', [
    'Abc9Xq7Lp4Vm2Ns8Rt5Zu0Yd3Kf6Gh1W/1.0',
    'User-Agent: Abc9Xq7Lp4Vm2Ns8Rt5Zu0Yd3Kf6Gh1W/1.0',
    'key=Abc9Xq%41Vm2Ns8Rt5Zu',
    'v=c0e0ed65d7a4b3f1e2a9c8b7a6d5e4f30146',
    'version=c0e0ed65d7a4b3f1e2a9c8b7a6d5e4f30146',
    '//example.com/api?token=Abc9Xq%41Vm2Ns8Rt5Zu',
    '/api/data?api_key=Abc9Xq%41Vm2Ns8Rt5Zu',
    'api_key=agent8088-demo/1.0',
])
def test_noise_exemptions_do_not_hide_credential_shapes(text):
    assert ss.find_secrets(text)


def test_cache_version_exemption_keeps_other_query_credentials():
    text = '/static/app.js?token=Abc9Xq7Lp4Vm2Ns8Rt5Zu0Yd3Kf6Gh1W&v=c0e0ed65d7a4b3f1e2a9c8b7a6d5e4f30146'
    assert ss.find_secrets(text)
