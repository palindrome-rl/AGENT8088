"""Docker sandbox images are provisioned before a command's own timeout starts.

`docker run` will pull a missing image itself, but inside the tool's timeout —
20s for the read-only git tools. The first git_status on a fresh machine then
fails with a bare "Command timed out after 20s" that mentions neither Docker nor
the pull, which is how a working feature looks broken.
"""


def _record_calls(engine, monkeypatch, responses):
    """Stub _exec_process, returning queued responses and recording argv/timeout."""
    calls = []

    def _fake(argv, timeout=25, shell=False):
        calls.append({"argv": argv, "timeout": timeout})
        return responses.pop(0) if responses else "Command completed."

    monkeypatch.setattr(engine, "_exec_process", _fake)
    return calls


def test_present_image_is_not_pulled(engine, monkeypatch):
    calls = _record_calls(engine, monkeypatch, ["present"])
    assert engine._ensure_docker_image("alpine/git:v2.47.2") == ""
    assert len(calls) == 1, "a local image must not trigger a pull"
    assert calls[0]["argv"][:3] == ["docker", "image", "inspect"]


def test_missing_image_is_pulled_before_use(engine, monkeypatch):
    calls = _record_calls(engine, monkeypatch, [
        "Error: No such image\nCommand exited with status 1.",
        "Status: Downloaded newer image",
    ])
    assert engine._ensure_docker_image("alpine/git:v2.47.2") == ""
    assert [c["argv"][:2] for c in calls] == [["docker", "image"], ["docker", "pull"]]


def test_pull_gets_its_own_budget_not_the_tool_timeout(engine, monkeypatch):
    """The whole point: the pull must not run inside the caller's 20s."""
    calls = _record_calls(engine, monkeypatch, [
        "Error: No such image\nCommand exited with status 1.", "Downloaded"])
    engine._ensure_docker_image("alpine/git:v2.47.2")
    pull = next(c for c in calls if c["argv"][:2] == ["docker", "pull"])
    assert pull["timeout"] == engine.DOCKER_PULL_TIMEOUT
    assert pull["timeout"] > 20


def test_failed_pull_reports_the_image_and_the_fix(engine, monkeypatch):
    _record_calls(engine, monkeypatch, [
        "Error: No such image\nCommand exited with status 1.",
        "network unreachable\nCommand exited with status 1.",
    ])
    problem = engine._ensure_docker_image("alpine/git:v2.47.2")
    assert "alpine/git:v2.47.2" in problem
    assert "docker pull" in problem, "the error must name the command that fixes it"


def test_timed_out_pull_is_reported_as_a_pull_failure(engine, monkeypatch):
    _record_calls(engine, monkeypatch, [
        "Error: No such image\nCommand exited with status 1.",
        "Command timed out after 300s.",
    ])
    assert "could not be pulled" in engine._ensure_docker_image("alpine/git:v2.47.2")


def test_presence_is_cached_across_calls(engine, monkeypatch):
    calls = _record_calls(engine, monkeypatch, ["present"])
    engine._ensure_docker_image("alpine/git:v2.47.2")
    engine._ensure_docker_image("alpine/git:v2.47.2")
    assert len(calls) == 1, "second call must not re-probe docker"


def test_docker_command_refuses_to_run_when_the_image_is_unavailable(engine, monkeypatch):
    monkeypatch.setattr(engine, "_ensure_docker_image",
                        lambda _image: "Error: container image x is missing")
    ran = []
    monkeypatch.setattr(engine, "_exec_process",
                        lambda *a, **k: ran.append(a) or "should not happen")
    out = engine._exec_docker_command("git status", timeout=20, image="alpine/git:v2.47.2")
    assert "missing" in out
    assert ran == [], "no container may be started when the image is unavailable"
