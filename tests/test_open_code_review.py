import json
import sys
import time

import pytest

from agent8088 import open_code_review as review


@pytest.mark.parametrize("ref", ["--help", "HEAD;echo bad", "a b", "", "$(whoami)"])
def test_ref_rejects_command_input(ref):
    with pytest.raises(ValueError):
        review.validate_ref(ref)


@pytest.mark.parametrize("path", ["../secret", ".git/config", "C:/secret"])
def test_path_rejects_escape(tmp_path, path):
    with pytest.raises(ValueError):
        review.checked_path(tmp_path, path)


def test_preparation_is_not_review(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "executable", lambda config: "ocr")
    responses = iter(["open-code-review v1.12.1", json.dumps({
        "schema_version": "1", "reviewable_files": [{"path": "main.py", "status": "added"}]}),
        "@@ -1 +1 @@" + chr(10) + "+x = 1" + chr(10),
        json.dumps({"schema_version": "1", "groups": []})])
    result = review.prepare(tmp_path, {}, {}, permitted=lambda p: True,
                            run=lambda argv, cwd: next(responses))
    assert result["status"] == "prepared"
    assert result["reviewed_files"] == 0


def test_protected_source_never_reaches_rule_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "executable", lambda config: "ocr")
    responses = iter(["open-code-review v1.12.1", json.dumps({
        "schema_version": "1", "reviewable_files": [{"path": "secret.py"}]})])
    with pytest.raises(ValueError, match="protected"):
        review.prepare(tmp_path, {}, {}, permitted=lambda p: False,
                       run=lambda argv, cwd: next(responses))


def test_disabled_by_default(engine):
    result = engine.run_tool("review_code", {})
    assert "code_review_disabled" in result


def test_plan_mode_does_not_launch(engine, monkeypatch):
    engine.APP_CONFIG["open_code_review_enabled"] = "1"
    monkeypatch.setattr(engine, "PERMISSION_MODE", "plan-only")
    monkeypatch.setattr(review, "prepare", lambda *a, **k: pytest.fail("must not run"))
    assert "plan" in engine.run_tool("review_code", {}).lower()


def test_process_cancel_reaps_child(tmp_path):
    started = time.monotonic()
    killed = []
    def check():
        if time.monotonic() - started > 0.15:
            raise RuntimeError("cancelled")
    def kill(process):
        process.kill()
        process.wait(timeout=5)
        killed.append(process.returncode)
    with pytest.raises(RuntimeError, match="cancelled"):
        review.run_process([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path,
                           check=check, kill=kill)
    assert killed
    assert time.monotonic() - started < 5


def test_subprocess_failure_is_not_valid_json(tmp_path):
    with pytest.raises(ValueError, match="status 2"):
        review.run_process([sys.executable, "-c", "raise SystemExit(2)"], tmp_path,
                           check=lambda: None, kill=lambda p: p.kill())


def test_prepared_scope_carries_the_diff_it_selected(tmp_path, monkeypatch):
    """Selection without the diff makes the host agent go and find it again.

    preview reports path/status/insertions/deletions and no content, so a live
    review spent nine execute_shell calls rebuilding the diff -- and reported
    that git was unavailable in the sandbox, which is the normal case for a
    review running under isolation. The diff is the evidence the review is
    about; withholding it is what turns preparation into a scavenger hunt.
    """
    import json as _json
    from agent8088 import open_code_review as R
    monkeypatch.setattr(R, "executable", lambda config: "ocr")

    (tmp_path / "billing.py").write_text("x = 1\n", encoding="utf-8")
    preview = {"schema_version": "1", "reviewable_files": [
        {"path": "billing.py", "status": "modified", "insertions": 8, "deletions": 0}]}

    def fake_run(argv, cwd):
        if argv[1:2] == ["version"]:
            return f"open-code-review v{R.PINNED_VERSION} (abc) windows/amd64"
        if "preview" in argv:
            return _json.dumps(preview)
        if "rule" in argv:
            return _json.dumps({"schema_version": "1", "groups": []})
        if argv[0] == "git":
            return "@@ -1,3 +1,5 @@\n+def refund(a, p):\n+    return (a * p) // 100\n"
        raise AssertionError(f"unexpected argv {argv}")

    result = R.prepare(tmp_path, {"scope": "workspace"},
                       {"open_code_review_executable": "ocr"},
                       permitted=lambda p: True, run=fake_run)

    entry = result["scope"]["reviewable_files"][0]
    assert "// 100" in entry["diff"], "the selected file's diff is not in the payload"


def test_attached_diffs_are_bounded(tmp_path, monkeypatch):
    """A whole-repository review must not paste the repository into the turn."""
    import json as _json
    from agent8088 import open_code_review as R
    monkeypatch.setattr(R, "executable", lambda config: "ocr")

    files = [{"path": f"f{n}.py", "status": "modified", "insertions": 1, "deletions": 0}
             for n in range(40)]
    for f in files:
        (tmp_path / f["path"]).write_text("x\n", encoding="utf-8")

    def fake_run(argv, cwd):
        if argv[1:2] == ["version"]:
            return f"open-code-review v{R.PINNED_VERSION} (abc) windows/amd64"
        if "preview" in argv:
            return _json.dumps({"schema_version": "1", "reviewable_files": files})
        if "rule" in argv:
            return _json.dumps({"schema_version": "1", "groups": []})
        if argv[0] == "git":
            return "+" + ("A" * 40_000) + "\n"
        raise AssertionError(argv)

    result = R.prepare(tmp_path, {"scope": "workspace"},
                       {"open_code_review_executable": "ocr"},
                       permitted=lambda p: True, run=fake_run)

    total = sum(len(f.get("diff") or "") for f in result["scope"]["reviewable_files"])
    assert total <= R.MAX_DIFF_BYTES, f"attached diffs total {total} bytes"
    assert result["scope"]["diff_truncated"] is True


def _native_payload():
    return {"status": "complete", "llm": {"model": "glm-5.3"},
            "session_id": "sess-1",
            "summary": {"files_reviewed": 2, "comments": 2, "input_tokens": 51493,
                        "output_tokens": 47805, "total_tokens": 99298},
            "comments": [
                {"path": "billing.py", "start_line": 16, "end_line": 16, "severity": "high",
                 "category": "bug", "content": "Floor division truncates refunds.",
                 "existing_code": "return (amount * percent) // 100",
                 "suggestion_code": "return round(amount * percent / 100, 2)"},
                {"path": "../../etc/passwd", "start_line": 1, "end_line": 1, "severity": "low",
                 "category": "style", "content": "escapes the repository"},
            ]}


def test_native_findings_are_normalised_and_positions_validated(tmp_path, monkeypatch):
    """The rest of Agent8088 must not bind to OpenCodeReview's evolving schema.

    A finding whose path escapes the repository is the one that must never be
    silently trusted: it is the input that would let review output steer a
    later fix at an arbitrary file. Keep it as a warning, never as a finding.
    """
    import json as _json
    from agent8088 import open_code_review as R
    monkeypatch.setattr(R, "executable", lambda config: "ocr")
    (tmp_path / "billing.py").write_text("x\n" * 40, encoding="utf-8")

    result = R.review(tmp_path, {"scope": "workspace"}, {},
                      credentials={"url": "https://x/v1", "token": "T", "model": "glm-5.3"},
                      permitted=lambda p: True,
                      run=lambda argv, cwd, env=None: (
                          f"open-code-review v{R.PINNED_VERSION} (a) windows/amd64"
                          if argv[1:2] == ["version"] else _json.dumps(_native_payload())))

    assert result["status"] == "complete"
    assert result["engine_version"] == R.PINNED_VERSION
    assert [f["path"] for f in result["findings"]] == ["billing.py"]
    assert result["findings"][0]["severity"] == "high"
    assert result["findings"][0]["position_valid"] is True
    assert result["findings"][0]["verification"] == "unverified"
    assert any("escape" in w.lower() for w in result["warnings"]), result["warnings"]
    assert result["usage"]["input_tokens"] == 51493


def test_native_credentials_never_reach_argv_or_output(tmp_path, monkeypatch):
    """The token goes through a transient environment, never the command line."""
    import json as _json
    from agent8088 import open_code_review as R
    monkeypatch.setattr(R, "executable", lambda config: "ocr")
    seen = {}

    def fake_run(argv, cwd, env=None):
        seen.setdefault("argv", []).append(list(argv))
        seen["env"] = dict(env or {})
        if argv[1:2] == ["version"]:
            return f"open-code-review v{R.PINNED_VERSION} (a) windows/amd64"
        return _json.dumps({"status": "complete", "comments": [], "summary": {}})

    result = R.review(tmp_path, {"scope": "workspace"}, {},
                      credentials={"url": "https://x/v1", "token": "SUPERSECRET", "model": "m"},
                      permitted=lambda p: True, run=fake_run)

    assert all("SUPERSECRET" not in " ".join(a) for a in seen["argv"])
    assert seen["env"]["OCR_LLM_TOKEN"] == "SUPERSECRET"
    assert seen["env"]["OCR_CONFIG_PATH"], "config must be redirected away from the user's own"
    assert "SUPERSECRET" not in _json.dumps(result)
