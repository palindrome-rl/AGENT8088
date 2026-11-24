"""A keyword argument or setting whose value is a constant or a name is not a credential, and a shape that only looks
like a key does not end a run NOT VERIFIED.

Seen in a run: `creationflags=CREATE_BREAKAWAY_FROM_JOB | CREATE_NEW_PROCESS_GROUP,` in a launcher script was reported
as a credential written in clear text. The scan splits on spaces but keeps `=` inside a token, so the token was
`creationflags=CREATE_BREAKAWAY_FROM_JOB`: long enough for the blob rule, and the `=` of the assignment satisfied its
"carries base64 punctuation" clause. Any long `name=VALUE` in code has that shape (`retry_policy=exponential_backoff`,
`callback=handle_download_complete`).

Two fixes. A `name=value` token whose name is not a credential name is judged by its value alone. And at the end of a
run, a finding that is only weak (no digit in the string, so it reads as words) is not a reason for the NOT VERIFIED
headline: the in-run check already asks about it once, and what it leaves is "taken as the model's answer".
"""
from agent8088 import completion
from agent8088 import secret_scan as scan
from agent8088.trajectory import TrajectoryState

FAKE_KEY = "sk-proj-AbCdEf0123456789AbCdEf0123456789"


def test_keyword_arguments_with_constants_or_names_are_not_credentials():
    for line in (
        "creationflags=CREATE_BREAKAWAY_FROM_JOB | CREATE_NEW_PROCESS_GROUP,",
        "timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS",
        "mode=FILE_FLAG_BACKUP_SEMANTICS",
        "retry_policy=exponential_backoff_with_jitter",
        "callback=handle_download_complete_event",
        "stdout=subprocess.PIPE",
    ):
        assert scan.find_secrets(line) == [], line


def test_a_value_that_is_a_credential_is_still_found_behind_an_ordinary_name():
    value = "Zx9Qm4Lp2Vb7Nc1Td8Rw3Ks6"
    assert scan.find_secrets(f"seed={value}")
    assert scan.find_secrets(f"blob=dGhpcyBpcyBhIHRlc3Qgb2YgcGFkZGluZw==")
    assert scan.find_secrets(f"signature=Zm9vYmFyYmF6cXV4cXV1eA==")


def test_a_credential_name_keeps_the_whole_token_judged():
    value = "Zx9Qm4Lp2Vb7Nc1Td8Rw3Ks6"
    for name in ("api_key", "token", "client_secret", "auth_header", "db_password"):
        assert scan.find_secrets(f"{name}={value}"), name
    # A word-only value behind a credential name was reported before and still is.
    assert scan.find_secrets("session_secret=AbCdEfGhIjKlMnOpQrStUvWx")


def test_vendor_prefixed_keys_are_found_wherever_they_are():
    assert scan.find_secrets(f"launch(callback={FAKE_KEY})")
    assert scan.find_secrets(f"x = '{FAKE_KEY}'")


def _written(tmp_path, content):
    target = tmp_path / "notes.txt"
    target.write_text(content, encoding="utf-8")
    state = TrajectoryState(None)
    state.after_tool(state.before_tool("write_file"), "Wrote file", failed=False,
                     blocked=False, mutated=True, path=str(target))
    return state


def _arm(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])


def _credential_reasons(engine, state):
    reasons = engine._record_unmet_checks(completion.ContinuationBudget(2), state,
                                          reply="Done", request="Save a note", depth=0)
    return [r for r in reasons if "credential" in r]


def test_a_weak_finding_alone_is_not_a_reason_to_end_the_run_unverified(engine, monkeypatch, tmp_path):
    _arm(engine, monkeypatch, tmp_path)
    state = _written(tmp_path, "session_secret=AbCdEfGhIjKlMnOpQrStUvWx\n")
    assert scan.scan_files([tmp_path / "notes.txt"])      # the scan itself still reports it, as weak
    assert all(f.weak for _p, _l, f in scan.scan_files([tmp_path / "notes.txt"]))
    assert _credential_reasons(engine, state) == []
    assert not any("credential" in text for text in state.data.get("unmet_checks", []))


def test_a_strong_finding_still_ends_the_run_unverified(engine, monkeypatch, tmp_path):
    _arm(engine, monkeypatch, tmp_path)
    state = _written(tmp_path, f"key = '{FAKE_KEY}'\n")
    assert _credential_reasons(engine, state)


def test_a_strong_finding_beside_a_weak_one_is_still_a_reason(engine, monkeypatch, tmp_path):
    _arm(engine, monkeypatch, tmp_path)
    state = _written(tmp_path, f"session_secret=AbCdEfGhIjKlMnOpQrStUvWx\nkey = '{FAKE_KEY}'\n")
    assert _credential_reasons(engine, state)
