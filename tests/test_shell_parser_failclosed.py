"""Shell detection must not depend on the command being well-formed.

`_hard_blocked_shell` lexes the command with shlex to find dangerous git
operations and wrapper payloads. When shlex raised, the function returned False
and every lexer-based check below it silently passed — so appending a single
unbalanced quote bypassed the always-on git floor.

Hermes handles this with `_command_parser_limit_exceeded` (an unparseable
command is treated as dangerous) and `_command_detection_variants` (detection is
retried on normalized variants). This suite pins the same properties.
"""
import pytest

from agent8088 import engine as A


# --- The original bypass ---------------------------------------------------

@pytest.mark.parametrize("command", [
    'git push origin main "',
    "git push origin main '",
    'git reset --hard HEAD "',
    'git clean -fd "',
    "git branch -D main '",
    'git checkout -- . "',
    'git stash drop "',
])
def test_unbalanced_quote_does_not_bypass_the_git_floor(command):
    """Each of these is refused without the trailing quote; it must stay refused."""
    assert A._hard_blocked_shell(command) is True


def test_well_formed_equivalents_are_still_refused():
    """Sanity: the guard being tested actually fires on the clean form."""
    assert A._hard_blocked_shell("git push origin main") is True
    assert A._hard_blocked_shell("git reset --hard HEAD") is True


def test_unbalanced_quote_in_a_wrapper_payload_is_caught():
    assert A._hard_blocked_shell("""bash -c 'git push origin main "'""") is True


# --- Fail closed when nothing parses ---------------------------------------

def test_quote_only_string_carries_no_command_and_is_not_refused():
    """A string with no executable tokens is not a bypass, so not a block.

    A run of bare quote characters de-quotes to whitespace and lexes to nothing —
    a real shell rejects it outright. Refusing it would be a false positive; the
    property that matters is that de-quoting *preserves* real tokens, which the
    tests above cover.
    """
    assert A._hard_blocked_shell('"' * 3) is False


def test_pathologically_long_command_is_refused():
    """A command past the parser limit is treated as dangerous, not skipped."""
    assert A._hard_blocked_shell("echo " + "a" * (A.MAX_COMMAND_CHARS + 1)) is True


def test_deeply_nested_quotes_are_refused():
    assert A._hard_blocked_shell("bash -c " + "'\"" * 200) is True


# --- No false positives on ordinary commands -------------------------------

@pytest.mark.parametrize("command", [
    "ls -la",
    "git status",
    "git diff --stat",
    "echo 'hello world'",
    'echo "hello world"',
    """awk '{print $1}' file.txt""",
    """sed -i '' 's/a/b/' file.txt""",
    'grep -r "TODO" src/',
    """python -c 'print("hi")'""",
    "git commit -m 'fix: it\"s quoted oddly'",
])
def test_ordinary_commands_are_not_refused(command):
    assert A._hard_blocked_shell(command) is False


def test_quote_stripping_does_not_invent_a_git_command():
    """A quoted literal mentioning a dangerous op must not become one.

    `echo "git push"` is a string, not a push. The de-quoted variant is only
    used to re-run detection, and echo/printf stay on the non-exec list.
    """
    assert A._hard_blocked_shell('echo "git push origin main"') is False
    assert A._hard_blocked_shell("printf 'git push'") is False


# --- The variant helper itself ---------------------------------------------

def test_variants_include_the_original_first():
    variants = list(A._command_detection_variants("git status"))
    assert variants[0] == "git status"


def test_variants_include_a_dequoted_form_when_quotes_are_present():
    variants = list(A._command_detection_variants('git push "'))
    assert len(variants) > 1
    assert any("git push" in v and '"' not in v for v in variants)


def test_variants_are_just_the_original_when_no_quotes():
    assert list(A._command_detection_variants("ls -la")) == ["ls -la"]


def test_parser_limit_detects_oversize_and_quote_storms():
    assert A._command_parser_limit_exceeded("x" * (A.MAX_COMMAND_CHARS + 1)) is True
    assert A._command_parser_limit_exceeded("ls -la") is False


# --- readonly mode must not be loosened either -----------------------------

def test_unparseable_command_is_not_readonly_safe():
    """_readonly_shell already fails closed; confirm it stays that way."""
    assert A._readonly_shell('ls "') is False


def test_unparseable_command_refused_in_full_auto(engine, monkeypatch):
    """full-auto skips check_permission, so the floor is the only thing left."""
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    result = engine.run_tool("execute_shell", {"command": 'git push origin main "'})
    assert "forbidden" in result.lower()
