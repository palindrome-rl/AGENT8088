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


def test_repository_not_found_is_not_answered_with_bare_credentials_advice(engine):
    """This assertion was wrong in both directions before it was right.

    Originally "not found" got the blanket credentials advice, which is wrong
    when the URL is simply mistyped. The first correction ruled credentials out
    entirely, which is wrong the other way -- GitHub returns "not found" for a
    private repository you cannot see. The answer is to present both causes and
    say the error cannot distinguish them.
    """
    hinted = engine._git_auth_hint(REPO_NOT_FOUND)
    assert "two possible causes" in hinted.lower()
    assert "git_remote" in hinted, "the wrong-url path must be offered"
    assert "gh auth login" in hinted, "the no-access path must be offered too"


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


# --- consent must name, and be bound to, the real target -------------------

def _escalation_for(engine, args):
    """The escalation text git_push raises for these arguments."""
    engine._remote_git_grant = False
    return engine.run_tool("git_push", args)


def test_the_approval_prompt_names_the_actual_remote_and_branch(engine):
    """The prompt is the only thing the user reads before consenting.

    Worse than cosmetic: on approval the harness tells the model to "retry the
    EXACT same tool call", and a model that reads `origin HEAD` in the prompt
    complies with *that* rather than its own arguments. A prompt naming the
    wrong remote steers the retry to the wrong remote.
    """
    text = _escalation_for(engine, {"remote": "production", "branch": "main"})
    assert "production" in text, f"prompt does not name the real remote: {text[:300]}"
    assert "main" in text
    assert "origin HEAD" not in text


def test_the_default_push_still_reads_as_origin(engine):
    text = _escalation_for(engine, {})
    assert "origin" in text and "HEAD" in text


def test_an_approval_does_not_authorise_a_different_target(engine, monkeypatch):
    """A bare boolean grant let an approval for one remote push to another."""
    calls = []
    monkeypatch.setattr(engine, "_exec_structured_tool",
                        lambda name, args, timeout: calls.append(args) or "pushed")

    engine._remote_git_grant = False
    engine.run_tool("git_push", {"remote": "staging"})      # asks
    engine._remote_git_grant = True                          # user approves *staging*
    out = engine.run_tool("git_push", {"remote": "production"})  # different target

    assert not calls, (
        "an approval for one remote authorised a push to a different one")
    assert "ESCALATION_REQUEST" in out, "the changed target must re-ask"


def test_repository_not_found_keeps_permissions_on_the_table(engine):
    """GitHub answers 404 for a private repo you cannot see.

    It does that deliberately, so a stranger cannot probe which private repos
    exist. So "Repository not found" means *either* a wrong URL *or* no access
    — asserting it is definitely not an auth problem is as wrong as the
    original blanket credentials advice, just in the other direction.
    """
    hinted = engine._git_auth_hint(REPO_NOT_FOUND)
    low = hinted.lower()
    assert "not an authentication problem" not in low, (
        "ruled out permissions for an error GitHub also returns when you lack access")
    assert "access" in low or "permission" in low, (
        "does not mention the access possibility at all")
    assert "git_remote" in hinted, "still needs the wrong-url path"


def test_unambiguous_causes_still_rule_out_auth(engine):
    """A missing remote or a dead host really is not about credentials."""
    for text in (MISSING_REMOTE, "fatal: unable to access: Could not resolve host: nope\n"):
        assert "not an authentication problem" in engine._git_auth_hint(text).lower()


# --- a missing binary is not the same as a missing repository --------------

GH_REPO_MISSING = (
    "remote: Repository not found.\n"
    "fatal: repository 'https://github.com/x/y.git/' not found\n"
    "Command exited with status 128."
)
SHELL_NO_GIT = "sh: 1: git: not found\nCommand exited with status 127."


def test_a_missing_repository_is_not_reported_as_a_missing_binary(engine):
    """Found live: this replaced the real error, it did not merely add to it.

    GitHub's "Repository not found" contains the substring "not found", so the
    missing-binary hint claimed git was not installed and threw the actual
    error away. The agent then told the user its sandbox lacked git and gave
    up, with the push failure never surfacing.
    """
    out = engine._missing_binary_hint("git", GH_REPO_MISSING)
    assert "is not available where this tool ran" not in out, (
        "a missing repository was reported as a missing git binary")
    assert "Repository not found" in out, "the real error was discarded"


def test_a_genuinely_missing_binary_is_still_explained(engine):
    out = engine._missing_binary_hint("git", SHELL_NO_GIT)
    assert "is not available where this tool ran" in out
    assert "host=1" in out


def test_status_127_alone_still_counts(engine):
    out = engine._missing_binary_hint("soffice", "Command exited with status 127.")
    assert "is not available where this tool ran" in out


def test_the_prompt_reads_as_english_not_argv(engine):
    """A consent prompt is read by a human under time pressure.

    "Push to -u origin master?" leaks a git flag into the sentence. The target
    is origin master; setting upstream is a property of the push, so it is
    said as one.
    """
    text = _escalation_for(engine, {"set_upstream": True, "branch": "main"})
    assert "-u origin" not in text, f"argv leaked into the prompt: {text[:200]}"
    assert "origin main" in text
    assert "upstream" in text.lower(), "the extra effect must still be disclosed"
