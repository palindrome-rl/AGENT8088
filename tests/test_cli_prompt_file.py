"""--prompt-file: one headless turn with the file's full text, then exit.

Piped stdin splits a multi-line instruction into one turn per line; an
evaluation harness needs the whole instruction delivered as one turn."""
import importlib
import sys

import pytest


@pytest.fixture
def cli(engine):
    from agent8088 import cli as module
    return importlib.reload(module)


def _run(cli, monkeypatch, argv):
    seen = []
    monkeypatch.setattr(cli, "do_chat", lambda query: seen.append(query))
    monkeypatch.setattr(cli, "banner", lambda: pytest.fail("banner must not print"))
    monkeypatch.setattr(cli.A, "resolve_auto_search_provider",
                        lambda: pytest.fail("no search probe in one-shot mode"))
    monkeypatch.setattr(sys, "argv", ["agent8088", *argv])
    return cli.main(), seen


def test_prompt_file_runs_one_turn_with_full_text(cli, monkeypatch, tmp_path):
    prompt = tmp_path / "instruction.md"
    prompt.write_text("Fix the bug.\n\nreset\n/help is not a command here\n", encoding="utf-8")
    monkeypatch.setattr(cli.A, "DISPOSABLE_CONTAINER", True)
    monkeypatch.setattr(cli.A, "verify_sandbox_backend",
                        lambda: pytest.fail("container is the sandbox"))
    rc, seen = _run(cli, monkeypatch, ["--prompt-file", str(prompt)])
    assert rc == 0
    assert seen == ["Fix the bug.\n\nreset\n/help is not a command here"]
    assert cli.A.PERMISSION_MODE == "full-auto"


def test_prompt_file_outside_container_still_verifies_sandbox(cli, monkeypatch, tmp_path):
    prompt = tmp_path / "p.md"
    prompt.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(cli.A, "DISPOSABLE_CONTAINER", False)
    verified = []
    monkeypatch.setattr(cli.A, "verify_sandbox_backend", lambda: verified.append(True))
    rc, seen = _run(cli, monkeypatch, ["--prompt-file", str(prompt)])
    assert rc == 0 and seen == ["hello"] and verified == [True]


def test_missing_prompt_file_exits_2(cli, monkeypatch, tmp_path):
    rc, seen = _run(cli, monkeypatch, ["--prompt-file", str(tmp_path / "nope.md")])
    assert rc == 2 and seen == []
