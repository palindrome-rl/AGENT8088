import json
import sys
import time
from pathlib import Path

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
        tools = argv[argv.index("--tools") + 1]
        seen["tools"] = _json.loads(Path(tools).read_text(encoding="utf-8"))
        return _json.dumps({"status": "complete", "comments": [], "summary": {}})

    result = R.review(tmp_path, {"scope": "workspace"}, {},
                      credentials={"url": "https://x/v1", "token": "SUPERSECRET", "model": "m"},
                      permitted=lambda p: True, run=fake_run)

    assert all("SUPERSECRET" not in " ".join(a) for a in seen["argv"])
    assert seen["env"]["OCR_LLM_TOKEN"] == "SUPERSECRET"
    assert seen["env"]["OCR_CONFIG_PATH"], "config must be redirected away from the user's own"
    assert {item["name"] for item in seen["tools"]} == {
        "task_done", "code_comment", "file_read_diff"}
    assert "SUPERSECRET" not in _json.dumps(result)


def test_a_finding_survives_an_edit_above_it_but_not_one_on_it(tmp_path):
    """Re-validation compares the quoted code, not the line number.

    An edit higher in the file shifts every line below it without making any
    finding wrong; an edit on the cited line makes it wrong while the number
    still looks intact. Checking the number alone gets both cases backwards,
    and this is the gate a fix has to pass.
    """
    from agent8088 import open_code_review as R
    source = tmp_path / "billing.py"
    source.write_text("a = 1\nb = 2\nreturn (amount * percent) // 100\n", encoding="utf-8")
    finding = {"path": "billing.py", "start_line": 3,
               "existing_code": "return (amount * percent) // 100"}

    assert R.recheck_finding(tmp_path, finding) is True

    source.write_text("a = 1\nINSERTED = 0\nb = 2\nreturn (amount * percent) // 100\n",
                      encoding="utf-8")
    assert R.recheck_finding(tmp_path, finding) is True, "an edit above it must not invalidate it"

    source.write_text("a = 1\nb = 2\nreturn round(amount * percent / 100, 2)\n", encoding="utf-8")
    assert R.recheck_finding(tmp_path, finding) is False, "the cited line no longer reads that way"


def test_recheck_refuses_what_it_cannot_establish(tmp_path):
    """No quoted code, no file, no line -- all must be False, never optimistic."""
    from agent8088 import open_code_review as R
    (tmp_path / "x.py").write_text("a = 1\n", encoding="utf-8")
    assert R.recheck_finding(tmp_path, {"path": "x.py", "start_line": 1}) is False
    assert R.recheck_finding(tmp_path, {"path": "gone.py", "start_line": 1,
                                        "existing_code": "a = 1"}) is False
    assert R.recheck_finding(tmp_path, {"path": "../escape.py", "start_line": 1,
                                        "existing_code": "a = 1"}) is False
    assert R.recheck_finding(tmp_path, {"path": "x.py", "start_line": 99,
                                        "existing_code": "a = 1"}) is False


@pytest.mark.parametrize("url", [
    "https://github.com/o/r/pull/1?x=1",
    "https://user:token@github.com/o/r/pull/1",
    "https://evil.example/o/r/pull/1",
    "https://github.com/o/r/pull/1#frag",
    "https://github.com/../r/pull/1",
    "https://github.com/o/r/pulls/1",
    "",
])
def test_pull_request_urls_are_one_exact_shape(url):
    """A review clones and then runs a full pass over whatever comes back, so the
    URL is where a hostile value is cheapest to refuse."""
    with pytest.raises(ValueError):
        review.parse_pr(url)


def test_a_well_formed_pull_request_url_parses():
    assert review.parse_pr("https://github.com/palindrome-rl/Agent8088/pull/173") == (
        "palindrome-rl", "Agent8088", 173)


def test_pull_request_checkout_never_runs_hooks_or_submodules(tmp_path):
    """The fetch is the moment a hostile repository would get to execute."""
    calls = []
    def run(argv, cwd):
        calls.append(argv)
        if argv[0] == "gh":
            return json.dumps({"base": {"sha": "a" * 40}, "head": {"sha": "a" * 40}})
        return "a" * 40 + "\n"
    review.checkout_pr("https://github.com/o/r/pull/7", tmp_path / "wt",
                       run=run)

    flat = [" ".join(argv) for argv in calls]
    assert any("core.hooksPath=" in line for line in flat)
    assert any("--no-recurse-submodules" in line for line in flat)
    assert any("protocol.allow=never" in line for line in flat)
    assert any("gh api repos/o/r/pulls/7" in line for line in flat)
    assert any("merge-base " + "a" * 40 + " ocr-pr-head" in line for line in flat)
    assert any("checkout --quiet --detach ocr-pr-head" in line for line in flat)


def test_stderr_progress_does_not_corrupt_json(tmp_path):
    output = review.run_process(
        [sys.executable, "-c", "import sys; print('progress', file=sys.stderr); print('{}')"],
        tmp_path, check=lambda: None, kill=lambda p: p.kill())
    assert json.loads(output) == {}


@pytest.mark.parametrize("args", [
    {"scope": "range", "base": "main", "commit": "HEAD"},
    {"scope": "commit", "commit": "HEAD", "head": "main"},
])
def test_incompatible_scope_arguments(tmp_path, args):
    with pytest.raises(ValueError):
        review._scope_flags(tmp_path, args)


def test_end_line_must_exist(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    findings, _ = review.normalise({"comments": [
        {"path": "x.py", "start_line": 1, "end_line": 99}]}, tmp_path, permitted=lambda p: True)
    assert findings[0]["position_valid"] is False


def test_pr_failure_cleans_workspace_and_callback_exists(engine, monkeypatch, tmp_path):
    engine.APP_CONFIG["open_code_review_enabled"] = "1"
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "check_permission", lambda *a, **k: True)
    workspace = tmp_path / "pr-checkout"
    workspace.mkdir()
    monkeypatch.setattr(engine.tempfile, "mkdtemp", lambda **k: str(workspace))
    def checkout(url, destination, *, run):
        run(["git", "version"], workspace)
        raise ValueError("fetch failed")
    monkeypatch.setattr(review, "checkout_pr", checkout)
    output = engine.run_tool("review_code", {"pull_request": "https://github.com/o/r/pull/1"})
    assert "code_review_failed" in output
    assert not workspace.exists()


def test_reopen_does_not_use_unrelated_project(engine, monkeypatch, tmp_path):
    from agent8088 import review_store
    repo = tmp_path / "reviewed"
    repo.mkdir()
    (repo / "a.py").write_text("bad()\n", encoding="utf-8")
    monkeypatch.setattr(engine, "REVIEW_STORE_PATH", tmp_path / "reviews.db")
    monkeypatch.setattr(engine, "_path_is_allowed", lambda p: p == repo)
    monkeypatch.setattr(engine, "_is_sensitive_path", lambda p: False)
    key = review_store.save(engine.REVIEW_STORE_PATH, repo, {"findings": [
        {"path": "a.py", "start_line": 1, "existing_code": "bad()", "position_valid": True}]})
    assert engine.reopen_review(key)["findings"][0]["position_valid"]
    assert engine.reopen_review(key, root=tmp_path) is None


def test_a_stored_review_is_redacted_before_it_is_written(tmp_path, engine, monkeypatch):
    """The durable copy must not be less protected than the ephemeral one.

    A review deliberately quotes source lines, so a repository holding the
    user's own key in a config file produces a finding containing it. The tool
    result was redacted on the way out but the store was written first, so the
    secret persisted in reviews.db in plaintext while the model saw
    [redacted] -- backwards, and the durable artifact is the one that lasts.
    """
    secret = "sk-live-canary-9f3a1b7c-should-never-persist"
    monkeypatch.setattr(engine, "_SECRET_VALUES", [secret])
    monkeypatch.setattr(engine, "REVIEW_STORE_PATH", tmp_path / "reviews.db")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setitem(engine.APP_CONFIG, "open_code_review_enabled", "1")

    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])

    captured = {}

    def fake_review(root, args, config, *, credentials, permitted, run):
        return {"schema_version": 1, "status": "complete", "mode": "native",
                "engine": "open-code-review", "engine_version": review.PINNED_VERSION,
                "coverage": {"reviewed": 1, "findings": 1, "partial": False},
                "usage": {}, "findings": [
                    {"id": "a.py:1:0", "path": "a.py", "start_line": 1, "end_line": 1,
                     "severity": "high", "category": "security",
                     "message": "hard-coded credential " + secret,
                     "existing_code": 'KEY = "' + secret + '"',
                     "suggested_code": "", "position_valid": True,
                     "verification": "unverified"}],
                "warnings": []}

    monkeypatch.setattr(review, "review", fake_review)
    monkeypatch.setattr(engine, "_review_credentials",
                        lambda: {"url": "http://x/v1", "token": "t", "model": "m"})
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")

    returned = engine.run_tool("review_code", {"repo": str(tmp_path), "mode": "native"})
    assert secret not in returned, "the returned result leaked the secret"

    from agent8088 import review_store
    rows = review_store.recent(tmp_path / "reviews.db")
    assert rows, "the review was not stored at all"
    stored = review_store.load(tmp_path / "reviews.db", rows[0]["id"])
    captured["blob"] = __import__("json").dumps(stored)
    assert secret not in captured["blob"], "the STORED review kept the secret in plaintext"


def test_an_unresolvable_ref_is_named_before_the_engine_runs(tmp_path, monkeypatch):
    """"exited with status 1" does not tell the user which ref was wrong.

    A checkout commonly has origin/main but no local main, and that is the most
    likely thing a caller gets wrong. Resolving each ref first turns an opaque
    subprocess failure into the name of the ref that does not exist.
    """
    from agent8088 import open_code_review as R
    monkeypatch.setattr(R, "executable", lambda config: "ocr")

    def fake_run(argv, cwd, env=None):
        if argv[1:2] == ["version"]:
            return f"open-code-review v{R.PINNED_VERSION} (a) windows/amd64"
        if argv[0] == "git" and "rev-parse" in argv:
            raise ValueError("fatal: Needed a single revision")
        raise AssertionError("the engine must not be reached: " + " ".join(argv))

    with pytest.raises(ValueError, match="main"):
        R.review(tmp_path, {"scope": "range", "base": "main", "head": "HEAD"}, {},
                 credentials={"url": "u", "token": "t", "model": "m"},
                 permitted=lambda p: True, run=fake_run)


def test_the_suggested_ref_is_one_that_actually_resolves(tmp_path, monkeypatch):
    """The hint has to be usable, or it is a second dead end.

    Prefixing origin/ unconditionally told a live run that 'origin/main' should
    have been 'origin/origin/main'. It then burned a turn on that. Suggest an
    alternative only when it resolves, and otherwise say nothing specific.
    """
    from agent8088 import open_code_review as R
    monkeypatch.setattr(R, "executable", lambda config: "ocr")

    def run_with(existing):
        def fake_run(argv, cwd, env=None):
            if argv[1:2] == ["version"]:
                return f"open-code-review v{R.PINNED_VERSION} (a) windows/amd64"
            if argv[0] == "git" and "rev-parse" in argv:
                if argv[-1].removesuffix("^{commit}") in existing:
                    return "deadbeef"
                raise ValueError("fatal: Needed a single revision")
            raise AssertionError("the engine must not be reached")
        return fake_run

    def review(base, existing):
        return R.review(tmp_path, {"scope": "range", "base": base, "head": "HEAD"}, {},
                        credentials={"url": "u", "token": "t", "model": "m"},
                        permitted=lambda p: True, run=run_with(existing | {"HEAD"}))

    # origin/main is wrong and plain main exists -- strip, never double the prefix.
    with pytest.raises(ValueError) as wrong_way:
        review("origin/main", {"main"})
    assert "'main'" in str(wrong_way.value)
    assert "origin/origin/" not in str(wrong_way.value)

    # Nothing plausible resolves, so offer no specific ref at all.
    with pytest.raises(ValueError) as neither:
        review("nope", set())
    assert "Did you mean" not in str(neither.value)


def _finding(path, start, end, severity, message, code):
    return {"id": f"{path}:{start}:0", "path": path, "start_line": start,
            "end_line": end, "severity": severity, "category": "security",
            "message": message, "existing_code": code, "suggested_code": "",
            "position_valid": True, "verification": "unverified"}


def test_one_defect_reported_at_two_granularities_collapses():
    """Seven findings for four defects is not seven defects.

    OpenCodeReview reports a defect once against the enclosing function and
    once against the offending line. Live, four planted defects came back as
    seven findings, and summarising seven near-identical items pushed the
    model into inventing two functions the file does not contain.

    The quoted code is the discriminator, not the message: the two MD5
    findings scored 0.56 on message similarity while the pricing pairs scored
    0.93, so a text threshold separates neither reliably.
    """
    from agent8088.open_code_review import _dedupe

    wide = _finding("app/auth.py", 15, 17, "critical", "MD5 is cryptographically weak.",
                    "def reset_token(user_id):\n    return hashlib.md5(x).hexdigest()")
    narrow = _finding("app/auth.py", 17, 17, "high", "MD5 is cryptographically broken.",
                      "    return hashlib.md5(x).hexdigest()")
    separate = _finding("app/auth.py", 12, 12, "high", "`==` is not constant time.",
                        "    return sha256(token) == stored_hash")

    kept = _dedupe([wide, narrow, separate])

    assert len(kept) == 2, "the pair is one defect; the timing bug is another"
    assert kept[0]["severity"] == "critical", "never silently downgrade the louder one"
    assert separate in kept, "a genuinely different defect must survive"


def test_a_tie_on_severity_keeps_the_tighter_quote():
    """The narrow quote survives an edit elsewhere in the same function."""
    from agent8088.open_code_review import _dedupe

    wide = _finding("app/pricing.py", 20, 22, "high", "Floor division drops cents.",
                    "def loyalty_credit(a, p):\n    return (a * p) // 100")
    narrow = _finding("app/pricing.py", 22, 22, "high", "Floor division drops cents.",
                      "    return (a * p) // 100")

    kept = _dedupe([wide, narrow])

    assert len(kept) == 1
    assert kept[0]["start_line"] == 22


def test_two_defects_quoted_on_the_same_line_both_survive():
    """Identical ranges are left alone -- merging them would drop a real finding."""
    from agent8088.open_code_review import _dedupe

    injection = _finding("app/db.py", 8, 8, "critical", "SQL is built by concatenation.",
                         '    db.run("SELECT * FROM u WHERE t=" + tok)')
    secret = _finding("app/db.py", 8, 8, "high", "A token is interpolated into a log.",
                      '    db.run("SELECT * FROM u WHERE t=" + tok)')

    assert len(_dedupe([injection, secret])) == 2


def test_a_pr_workspace_spelled_differently_still_permits_its_own_files(
        engine, monkeypatch, tmp_path):
    """The permission gate and the adapter have to agree on what root is.

    mkdtemp inherits %TMP%, which on Windows is commonly the 8.3 short form
    of the user directory, while the adapter resolves root to the long form
    form before building finding paths. is_relative_to then failed for every
    file in the checkout, so each finding was dropped as "a protected path"
    and the PR review reported complete with nothing in it -- the agent, given
    an empty review, went off to fetch the diff by hand and ran out of turns.

    Two spellings of one directory stand in for the short/long pair here,
    because 8.3 names cannot be created portably.
    """
    engine.APP_CONFIG["open_code_review_enabled"] = "1"
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "check_permission", lambda *a, **k: True)
    monkeypatch.setattr(engine, "REVIEW_STORE_PATH", tmp_path / "reviews.db")

    workspace = tmp_path / "pr-checkout"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(engine.tempfile, "mkdtemp", lambda **k: str(workspace))

    unresolved = str(workspace / "src" / "..")
    monkeypatch.setattr(review, "checkout_pr",
                        lambda url, destination, *, run: {
                            "root": unresolved, "base": "b", "head": "h"})

    seen = {}

    def fake_review(root, args, config, *, credentials, permitted, run):
        seen["permitted"] = permitted
        return {"status": "complete", "findings": [], "warnings": [],
                "coverage": {"reviewed": 1, "findings": 0, "partial": False}}

    monkeypatch.setattr(review, "review", fake_review)
    monkeypatch.setattr(engine, "_review_credentials",
                        lambda: {"url": "u", "token": "t", "model": "m"})

    engine.run_tool("review_code", {"pull_request": "https://github.com/o/r/pull/1"})

    assert seen["permitted"](workspace / "src" / "a.py"), \
        "a file inside the PR checkout must not read as a protected path"
