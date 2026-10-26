"""Two ways a verdict said more than the run showed.

1. A run asked to build a web app wrote fetch scripts and no page or server, and was reported "exercised": the scripts ran,
   but the thing asked for did not exist. A level above "inspected" now needs an entry point (a page outside the reference
   folders, or a server file); the headline says that no runnable app was produced.
2. A page that loads its data from its own server passes the static page check without that server ever running, and the
   report said so only as "(5 warnings)". The answer now says the page's behaviour with its backend was not verified.
Both only lower or qualify a claim."""
from agent8088.trajectory import TrajectoryState

WEB_GOAL = "Create a Pokemon Doubles battle simulator web app, in which a player battles an AI. Search the web for data."


def _state(goal=WEB_GOAL, paths=(), **data):
    state = TrajectoryState({}, goal=goal, message_count=1)
    state.data.update({"mutation_count": 1, "ran_ok_at": 1, "written_paths": list(paths)})
    state.data.update(data)
    return state


def test_scripts_only_for_a_web_app_request_is_not_exercised(engine):
    state = _state(paths=["data/fetch_pokeapi.py", "scripts/build_data.py"])
    assert state.no_runnable_app()
    assert state.assurance() == "inspected"
    assert any("no runnable app" in r for r in engine._not_verified_reasons(state))


def test_a_tested_level_is_capped_too(engine):
    state = _state(paths=["fetch.py"], verification="verified")
    assert state.assurance() == "inspected"


def test_a_page_is_an_entry_point(engine):
    state = _state(paths=["app/index.html", "fetch.py"])
    assert not state.no_runnable_app()
    assert state.assurance() == "exercised"
    assert not any("no runnable app" in r for r in engine._not_verified_reasons(state))


def test_workspace_parent_names_never_hide_the_app():
    for workspace in ("C:/Users/Developer/Temp/project", "/tmp/data/project"):
        state = _state(paths=[workspace + "/index.html"])
        state.data["workspace"] = workspace
        assert state.has_app_entry()
        assert state.assurance() == "exercised"
        state.data["written_paths"] = [workspace + "/data/reference.html"]
        state.data["reference_paths"] = list(state.data["written_paths"])
        assert not state.has_app_entry()


def test_a_server_file_is_an_entry_point(engine):
    assert not _state(paths=["pokebattle/server.py"]).no_runnable_app()
    assert not _state(paths=["srv/app.py"]).no_runnable_app()


def test_a_saved_reference_page_is_not_an_entry_point(engine):
    state = _state(paths=["data/pikalytics.html", "research/docs.html", "fetch.py"])
    state.data["reference_paths"] = ["data/pikalytics.html", "research/docs.html"]
    assert state.no_runnable_app()
    assert state.assurance() == "inspected"


def test_authored_documentation_site_is_an_app_entry():
    state = _state(paths=["docs/index.html"])
    assert state.has_app_entry()
    assert state.assurance() == "exercised"


def test_other_requests_are_untouched(engine):
    state = _state(goal="Write a script that renames the files in a folder", paths=["rename.py"])
    assert not state.no_runnable_app()
    assert state.assurance() == "exercised"


def test_a_failed_run_stays_failed(engine):
    state = _state(paths=["fetch.py"], check_failing=True)
    assert state.assurance() == "failed"


def test_nothing_changed_has_no_level(engine):
    state = _state(paths=[], mutation_count=0)
    assert state.assurance() == ""
    assert not state.no_runnable_app()


def test_backend_note_reaches_the_answer(engine):
    state = _state(paths=["pokemon-battle/public/index.html"])
    assert state.backend_note() == ""
    state.note_backend_asked("E:/w/pokemon-battle/public/index.html")
    state.note_backend_asked("E:/w/pokemon-battle/public/index.html")
    assert "not verified" in state.backend_note() and "index.html" in state.backend_note()
    answer = engine._with_task_notes("Done.", state)
    assert "Not verified: the page calls its own server or a local service" in answer
    assert answer.count("index.html") == 1


def test_no_backend_note_without_a_backend_request(engine):
    assert "Not verified" not in engine._with_task_notes("Done.", _state(paths=["index.html"]))
