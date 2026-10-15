"""check_app as a tool and as a step of the completion pipeline."""
import json
from pathlib import Path
from types import SimpleNamespace as N

import pytest

from agent8088 import app_check, trajectory
from tests.test_app_check import OVERLAY, WORKING, chromium, needs_chromium  # noqa: F401


def _reply(content):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="stop")])


def _call(name, **args):
    return f"✿FUNCTION✿: {name} ✿ARGS✿: {json.dumps(args)}"


@pytest.fixture
def eng(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path / "artifacts"
    return engine


class Pipeline:
    """A scripted model plus a write tool that really writes, and a stubbed smoke test."""

    def __init__(self, engine, monkeypatch, tmp_path, replies, verdicts):
        self.engine, self.sent, self.calls, self.checked = engine, [], 0, []
        self.replies, self.verdicts = list(replies), list(verdicts)

        def completion(messages, *_a, **_k):
            self.sent.append(str(messages[-1].get("content") or ""))
            reply = self.replies[min(self.calls, len(self.replies) - 1)]
            self.calls += 1
            return _reply(reply)

        def tool(name, arguments, depth=0):
            args = json.loads(arguments)
            engine.note_mutation(name)
            path = Path(args["filename"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"], encoding="utf-8")
            return f"Wrote {len(args['content'])} bytes to {path}"

        def check(path, timeout=20, deny=None, **_kw):
            self.checked.append(Path(path).name)
            status = self.verdicts[min(len(self.checked) - 1, len(self.verdicts) - 1)]
            failures = ["clicking the first control (button \"Go\") changed nothing on the page"] if status == app_check.FAIL else []
            return app_check.Report("web", str(path), status, failures)

        monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
        monkeypatch.setattr(engine, "exec_tool", tool)
        monkeypatch.setattr(app_check, "check_target", check)
        self.page = str(tmp_path / "index.html")

    def run(self, request="build me a page"):
        return self.engine.run_agent([{"role": "user", "content": request}], max_turns=14,
                                     tools_def=[], allowed_tools={"write_file", "execute_shell"})

    def nudges(self):
        return [m for m in self.sent if "smoke test of what you built failed" in m]


def _write(page, n=0):
    return _call("write_file", filename=page, content=f"<html>{n}</html>")


def test_report_mode_shows_the_failure_in_the_answer_and_never_blocks(eng, monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "COMPLETION_SMOKE_TEST", "report")
    p = Pipeline(eng, monkeypatch, tmp_path, [None, "Your page is ready."], [app_check.FAIL])
    p.replies[0] = _write(p.page)
    answer = p.run()
    assert p.checked == ["index.html"]
    assert not p.nudges(), "report mode never sends the model back for a smoke-test failure"
    assert "Smoke test: index.html: smoke test FAILED" in answer and "changed nothing" in answer


def test_a_passing_page_leaves_a_short_note(eng, monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "COMPLETION_SMOKE_TEST", "report")
    p = Pipeline(eng, monkeypatch, tmp_path, [None, "Ready."], [app_check.PASS])
    p.replies[0] = _write(p.page)
    assert "index.html: smoke test passed" in p.run()


def test_enforce_mode_sends_the_failure_back_and_accepts_the_fix(eng, monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "COMPLETION_SMOKE_TEST", "enforce")
    p = Pipeline(eng, monkeypatch, tmp_path, [None, "Ready.", None, "Fixed."],
                 [app_check.FAIL, app_check.PASS])
    p.replies[0] = _write(p.page, 1)
    p.replies[2] = _write(p.page, 2)
    answer = p.run()
    nudges = p.nudges()
    assert len(nudges) == 1 and "changed nothing" in nudges[0] and "final answer" not in nudges[0].lower()
    assert "Continue: fix these problems" in nudges[0]
    assert p.checked == ["index.html", "index.html"]
    assert answer.startswith("Fixed.") and "smoke test passed" in answer


def test_enforce_mode_gives_up_after_two_failures_with_an_honest_note(eng, monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "COMPLETION_SMOKE_TEST", "enforce")
    replies = [None, "Ready.", None, "Ready now.", None, "Really ready."]
    p = Pipeline(eng, monkeypatch, tmp_path, replies, [app_check.FAIL])
    for n in (0, 2, 4):
        p.replies[n] = _write(p.page, n)
    answer = p.run()
    assert len(p.nudges()) == eng.COMPLETION_SMOKE_MAX_FAILURES == 2
    assert "smoke test FAILED" in answer and answer.startswith("Really ready.")


def test_off_skips_the_check(eng, monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "COMPLETION_SMOKE_TEST", "off")
    p = Pipeline(eng, monkeypatch, tmp_path, [None, "Ready."], [app_check.FAIL])
    p.replies[0] = _write(p.page)
    answer = p.run()
    assert p.checked == [] and "Smoke test" not in answer


def test_unchanged_work_is_not_checked_twice(eng, monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "COMPLETION_SMOKE_TEST", "report")
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    state.data["mutation_count"] = 3
    assert state.smoke_unchecked(["/w/a.html"]) == ["/w/a.html"]
    state.record_smoke("/w/a.html", "a.html: smoke test passed", failed=False)
    assert state.smoke_unchecked(["/w/a.html"]) == []
    state.data["mutation_count"] = 4
    assert state.smoke_unchecked(["/w/a.html"]) == ["/w/a.html"]


def test_unavailable_and_skipped_results_do_not_block_or_fail(eng, monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "COMPLETION_SMOKE_TEST", "enforce")
    p = Pipeline(eng, monkeypatch, tmp_path, [None, "Ready."], [app_check.UNAVAILABLE])
    p.replies[0] = _write(p.page)
    answer = p.run()
    assert not p.nudges() and "smoke test unavailable" in answer


def test_a_notes_line_replaces_the_older_one_for_the_same_file():
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    state.record_smoke("/w/a.html", "a.html: smoke test FAILED - x", failed=True)
    state.record_smoke("/w/a.html", "a.html: smoke test passed", failed=False)
    assert state.smoke_review() == "a.html: smoke test passed"
    assert state.smoke_failures() == 1


def test_the_failure_count_resets_with_the_next_request():
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    state.record_smoke("/w/a.html", "a.html: smoke test FAILED - x", failed=True)
    state.data["verification"] = "pending"   # unresolved work carries the state over
    again = trajectory.TrajectoryState(state.data, goal="h", message_count=2)
    assert again.smoke_failures() == 0


# --- the tool ---------------------------------------------------------------------------------------

def test_check_app_is_a_registered_read_only_tool(engine):
    spec = engine.TOOL_SPECS["check_app"]
    assert spec["mode"] == "read_text" and spec["args"] == ["path"]
    assert engine.TOOL_REQUIRED_PARAMS["check_app"] == ["path"]


@needs_chromium
def test_the_tool_reports_a_working_and_a_broken_page(eng, tmp_path):
    good, bad = tmp_path / "good.html", tmp_path / "bad.html"
    good.write_text(WORKING, encoding="utf-8")
    bad.write_text(OVERLAY, encoding="utf-8")
    ok = eng.exec_tool("check_app", json.dumps({"path": str(good)}))
    broken = eng.exec_tool("check_app", json.dumps({"path": str(bad)}))
    assert "good.html: smoke test passed" in ok
    assert "bad.html: smoke test FAILED" in broken and "covered by div#modal" in broken


@pytest.mark.parametrize("mode", ["readonly", "plan-only", "edit", "full-auto"])
def test_the_tool_is_allowed_in_every_permission_mode(eng, tmp_path, mode):
    eng.PERMISSION_MODE = mode
    script = tmp_path / "tool.py"
    script.write_text("print(1)\n", encoding="utf-8")
    result = eng.exec_tool("check_app", json.dumps({"path": str(script)}))
    assert "tool.py: smoke test passed" in result


def test_the_tool_refuses_credential_files_and_missing_paths(eng, tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("KEY=1", encoding="utf-8")
    assert "sensitive" in eng.exec_tool("check_app", json.dumps({"path": str(secret)})).lower()
    assert "does not exist" in eng.exec_tool("check_app", json.dumps({"path": str(tmp_path / "nope.html")}))


def test_the_tool_refuses_paths_outside_the_allowed_workspace(eng, tmp_path):
    outside = tmp_path.parent / "elsewhere.py"
    outside.write_text("print(1)\n", encoding="utf-8")
    assert "not allowed" in eng.exec_tool("check_app", json.dumps({"path": str(outside)})).lower()


def test_the_tool_needs_a_path(eng):
    assert "was called with no arguments" in eng.exec_tool("check_app", "{}")


def test_the_output_of_a_failed_script_start_is_kept_for_diagnosis_and_bounded():
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    state.record_smoke("/w/a.py", "a.py: smoke test FAILED - x", True, "Traceback\n" + "x" * 2000)
    kept = state.data["smoke_output"]
    assert kept["/w/a.py"].startswith("Traceback") and len(kept["/w/a.py"]) <= 900
    for name in "bcdef":
        state.record_smoke(f"/w/{name}.py", f"{name}.py: smoke test FAILED - x", True, "out " + name)
    assert len(state.data["smoke_output"]) == 4 and "/w/a.py" not in state.data["smoke_output"]


def test_a_passing_check_stores_no_output_and_the_next_request_clears_it():
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    state.record_smoke("/w/a.html", "a.html: smoke test passed", False)
    assert not state.data.get("smoke_output")
    state.record_smoke("/w/a.py", "a.py: smoke test FAILED - x", True, "boom")
    state.data["verification"] = "pending"
    again = trajectory.TrajectoryState(state.data, goal="h", message_count=2)
    assert again.data["smoke_output"] == {}


def test_saved_diagnostics_preserve_the_clipped_error_tail():
    output = "starting\n" + "setup detail\n" * 100 + "RuntimeError: synthetic root cause"
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    clipped = app_check.clip_output(output)
    state.record_smoke("/w/app.py", "app.py: smoke test FAILED", True, clipped)
    assert state.data["smoke_output"]["/w/app.py"] == clipped
