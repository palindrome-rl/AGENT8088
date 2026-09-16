import importlib
import json

import pytest


@pytest.fixture
def cli(engine):
    from agent8088 import cli as module

    return importlib.reload(module)


def _empty_review():
    return json.dumps({
        "engine": "open-code-review",
        "engine_version": "1.12.1",
        "mode": "native",
        "coverage": {"reviewed": 0, "partial": False},
        "findings": [],
        "warnings": [],
        "usage": {},
    })


def test_review_command_accepts_a_quoted_windows_repo_path(cli, monkeypatch):
    captured = {}

    def run_tool(name, args):
        captured.update(name=name, args=args)
        return _empty_review()

    monkeypatch.setattr(cli.A, "run_tool", run_tool)
    cli.cmd_review(r'--repo "C:\Users\New User\My Repo" --mode native')

    assert captured == {
        "name": "review_code",
        "args": {
            "scope": "workspace",
            "repo": r"C:\Users\New User\My Repo",
            "mode": "native",
        },
    }


def test_review_command_with_only_to_infers_the_range_base(cli, monkeypatch):
    captured = {}

    def run_tool(name, args):
        captured.update(name=name, args=args)
        return _empty_review()

    monkeypatch.setattr(cli.A, "run_tool", run_tool)
    cli.cmd_review("--to feature/coupons")

    assert captured["args"] == {"scope": "range", "head": "feature/coupons"}


def test_review_command_reports_unclosed_quotes_without_running(cli, monkeypatch, capsys):
    called = False

    def run_tool(name, args):
        nonlocal called
        called = True
        return _empty_review()

    monkeypatch.setattr(cli.A, "run_tool", run_tool)
    cli.cmd_review('--repo "C:\\My Repo')

    assert called is False
    assert "Could not parse review arguments" in capsys.readouterr().out
