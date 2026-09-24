"""Read-only shell commands must not buy an auditor because of their quoting.

With `/audit on`, every top-level call the classifier calls mutating spawns an
auditor sub-agent. Live runs on 2026-09-24 audited plain reads because of
characters that change nothing:

- `grep -E "barcelona|real madrid|…" f`: a `|` inside quotes read as a pipe (102s)
- `grep -o 'Barcelona[^<]*' f`: a `<` inside quotes read as a redirect (47s)
- `sed -n '415,445p' f`: sed never counted as read-only (42s)
- `ls -l x 2>&1; wc -c x`: `2>&1` read as writing a file

In one five-minute run, 144 of 293 seconds went to auditing reads.
"""
import pytest

from tests.test_audit_loop_guards import BENIGN, HOSTILE

LIVE_READS = [
    'grep -n -i -E "barcelona|real madrid|atletico|sevilla" '
    '"/private/tmp/ws/artifacts/laliga_standings.html"',
    "grep -n -i -o 'Barcelona[^<]*' \"/private/tmp/ws/artifacts/la-liga-standings.html\"",
    'grep -n -i -m 5 "league table\\|Pos" /Users/me/artifacts/la_liga_2026_27.html',
    "sed -n '415,445p' \"/private/tmp/ws/artifacts/laliga_standings.html\"",
    "sed -n '7584p' \"/private/tmp/ws/artifacts/la-liga-standings.html\"",
    "ls -l artifacts/la_liga_2026_27.html 2>&1; wc -c artifacts/la_liga_2026_27.html",
]

MORE_READS = [
    "sed -n 1,5p f", "sed -n '$p' f", "sed '10q' f", "sed -n -E '3,9p' f",
    "grep 'a;b' f", "grep 'a&&b' f", "grep \"x>y\" f | head -3",
    "grep -c foo f 2>&1", "cat f 2>&1 | head", "sed -n '1,5p' f 2>&1",
    "sed 1,5p f 1>&2", "grep -E 'a|b' f | sed -n 1p",
]

QUOTING_WRITES = [
    # sed that writes, runs, or reads its script from elsewhere
    "sed -i '1d' f", "sed --in-place '1d' f", "sed -n '1w out' f",
    "sed -n 'w out' f", "sed 'e rm x' f", "sed -n '1p;w x' f",
    "sed -f script.sed f", "sed -n '1,5p' f > out.txt", "sed -n 1p f -i",
    # a real operator next to a quoted one still counts
    "grep 'a|b' f; rm x", "grep 'a|b' f && rm x", "grep \"a\" f | sh",
    "grep 'a' f > out", "ls 2>&1 > out.txt", "ls 2>&1 >> out.txt",
    # quoting cannot hide a substitution that the shell would run
    'grep "$(rm x)" f', 'grep "`rm x`" f',
    # unparseable quoting is not trusted
    "grep 'abc f; rm x", 'grep "abc f; rm x',
    # flags, streams and operands that write or run
    "sed -n 1p --in-place=.bak f", "sed -n 1p -- -i", "sed -z 1p f",
    "sed -n 1p f 2>&1 | tee out", "sed -n 1p f 2>&1>out", "sed -n \"1p\" f &>out",
    "grep 'a' f >&out", "sed -n $'1p\\nw x' f", "sed -n 'r /etc/passwd' f",
    "grep \"a\\\"|b\" f; rm x", "sed -n 1p f | sh", "sed -n '1,5p' f | xargs rm",
    "grep x f 2>&1 && git push",
]


def test_live_reads_are_not_audited(engine):
    audited = [c for c in LIVE_READS + MORE_READS if not engine._shell_call_is_read_only(c)]
    assert audited == [], f"reads still buying an auditor: {audited}"


@pytest.mark.parametrize("command", QUOTING_WRITES + HOSTILE)
def test_no_write_is_called_read_only(engine, command):
    assert not engine._shell_call_is_read_only(command)


def test_existing_reads_stay_read_only(engine):
    assert [c for c in BENIGN if not engine._shell_call_is_read_only(c)] == []


def test_the_permission_gate_is_unchanged(engine):
    """Only the audit predicate is relaxed; approval prompts stay as strict."""
    assert not engine._readonly_shell("sed -n '1,5p' f")
    assert not engine._readonly_shell('grep -E "a|b" f')
