"""Two gaps left by the git host-execution work.

1. The agent can create a repository but cannot give it a remote, and
   `git_push` is hardcoded to `origin`. So "start a repo and push it to
   GitHub" dead-ends: git_init succeeds, and nothing can push the result.

2. Git prints "Could not read from remote repository." for a missing remote,
   a typo'd URL and a deleted repo -- not just for an auth failure. Treating
   that line as an auth marker sends the user to `gh auth login` for problems
   credentials cannot fix.
"""
import pytest


# --- 2. the auth hint must not misdiagnose ---------------------------------

MISSING_REMOTE = (
    "fatal: 'origin' does not appear to be a git repository\n"
    "fatal: Could not read from remote repository.\n\n"
    "Please make sure you have the correct access rights\n"
    "and the repository exists.\n"
)
REPO_NOT_FOUND = (
    "remote: Repository not found.\n"
    "fatal: repository 'https://github.com/x/y.git/' not found\n"
)
REAL_AUTH = (
    "fatal: could not read Username for 'https://github.com': "
    "terminal prompts disabled\n"
)
REAL_AUTH_SSH = "git@github.com: Permission denied (publickey).\n"


def test_missing_remote_does_not_get_credentials_advice(engine):
    """The cause is a missing remote; credentials will not fix it."""
    hinted = engine._git_auth_hint(MISSING_REMOTE)
    assert "gh auth login" not in hinted, (
        "credentials advice given for a missing remote")


def test_repository_not_found_does_not_get_credentials_advice(engine):
    hinted = engine._git_auth_hint(REPO_NOT_FOUND)
    assert "gh auth login" not in hinted


@pytest.mark.parametrize("text", [REAL_AUTH, REAL_AUTH_SSH])
def test_genuine_auth_failures_still_get_advice(engine, text):
    """The narrowing must not disarm the hint where it was right."""
    assert "gh auth login" in engine._git_auth_hint(text)


def test_missing_remote_says_what_would_actually_help(engine):
    """A wrong diagnosis is worse than none, but none is still not useful."""
    hinted = engine._git_auth_hint(MISSING_REMOTE)
    assert hinted != MISSING_REMOTE, "no guidance at all for a fixable problem"
    assert "git_remote" in hinted


# --- 1. remotes must be reachable from the agent ---------------------------

def test_git_remote_tool_exists(engine):
    assert "git_remote" in engine.TOOL_SPECS
    assert engine.TOOL_SPECS["git_remote"].get("host"), (
        "remote management touches the real repo, so it runs on the host")


@pytest.mark.parametrize("args,expected", [
    ({"action": "list"}, ["git", "remote", "-v"]),
    ({"action": "add", "name": "origin", "url": "https://example.com/r.git"},
     ["git", "remote", "add", "origin", "https://example.com/r.git"]),
    ({"action": "set-url", "name": "upstream", "url": "git@h:o/r.git"},
     ["git", "remote", "set-url", "upstream", "git@h:o/r.git"]),
])
def test_git_remote_argv(engine, args, expected):
    assert engine._structured_tool_argv("git_remote", args) == expected


@pytest.mark.parametrize("bad", [
    {"action": "add", "name": "origin"},                     # no url
    {"action": "add", "url": "https://example.com/r.git"},   # no name
    {"action": "rm -rf /", "name": "o", "url": "u"},         # not an action
    {"action": "add", "name": "--upload-pack=x", "url": "u"},  # flag as name
    {"action": "add", "name": "o", "url": "--upload-pack=x"},  # flag as url
])
def test_git_remote_rejects_bad_input(engine, bad):
    with pytest.raises(ValueError):
        engine._structured_tool_argv("git_remote", bad)


def test_git_push_still_defaults_to_origin_head(engine):
    assert engine._structured_tool_argv("git_push", {}) == [
        "git", "push", "origin", "HEAD"]


@pytest.mark.parametrize("args,expected", [
    ({"remote": "upstream"}, ["git", "push", "upstream", "HEAD"]),
    ({"branch": "main"}, ["git", "push", "origin", "main"]),
    ({"remote": "upstream", "branch": "main"},
     ["git", "push", "upstream", "main"]),
    ({"set_upstream": True}, ["git", "push", "-u", "origin", "HEAD"]),
])
def test_git_push_accepts_a_remote_and_branch(engine, args, expected):
    assert engine._structured_tool_argv("git_push", args) == expected


@pytest.mark.parametrize("bad", [
    {"remote": "--upload-pack=touch /tmp/x"},
    {"branch": "--exec=whatever"},
    {"remote": "a\nb"},
])
def test_git_push_rejects_flags_and_control_characters(engine, bad):
    with pytest.raises(ValueError):
        engine._structured_tool_argv("git_push", bad)
