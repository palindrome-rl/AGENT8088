"""write_file mode='append': a long file written in parts behaves as one write."""
import json

import pytest


@pytest.fixture
def setup(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PLAN_AUDIT = False
    engine.reset_turn_counters()
    return engine, tmp_path


def _call(engine, **args):
    return engine.exec_tool("write_file", json.dumps(args))


def test_a_file_can_be_written_in_three_parts(setup):
    engine, tmp = setup
    target = tmp / "big.py"
    first = _call(engine, filename=str(target), content="# part 1\n")
    second = _call(engine, filename=str(target), content="# part 2\n", mode="append")
    third = _call(engine, filename=str(target), content="# part 3\n", mode="append")
    assert target.read_text(encoding="utf-8") == "# part 1\n# part 2\n# part 3\n"
    assert first.startswith("Wrote ") and second.startswith("Appended ") and third.startswith("Appended ")
    assert f"now {len(target.read_bytes())} bytes" in third


def test_append_creates_a_missing_file(setup):
    engine, tmp = setup
    target = tmp / "new.txt"
    assert _call(engine, filename=str(target), content="hello", mode="append").startswith("Appended ")
    assert target.read_text(encoding="utf-8") == "hello"


def test_default_mode_still_overwrites(setup):
    engine, tmp = setup
    target = tmp / "a.txt"
    _call(engine, filename=str(target), content="old old old")
    _call(engine, filename=str(target), content="new")
    assert target.read_text(encoding="utf-8") == "new"
    _call(engine, filename=str(target), content="newer", mode="overwrite")
    assert target.read_text(encoding="utf-8") == "newer"


def test_append_keeps_newlines_and_unicode_exact(setup):
    engine, tmp = setup
    target = tmp / "u.txt"
    _call(engine, filename=str(target), content="a\r\nb\n")
    _call(engine, filename=str(target), content="é\tz", mode="append")
    assert target.read_bytes() == "a\r\nb\né\tz".encode("utf-8")


@pytest.mark.parametrize("bad", ["prepend", "APPEND_ALL", 3])
def test_unknown_mode_is_refused_as_a_malformed_call(setup, bad):
    engine, tmp = setup
    target = tmp / "x.txt"
    result = _call(engine, filename=str(target), content="x", mode=bad)
    assert "invalid arguments" in result and "'mode' must be" in result
    assert not target.exists()
    assert engine._is_missing_argument_error("Error: " + result.split("Error: ", 1)[-1][:200]) or "invalid arguments" in result


def test_append_with_source_url_is_refused(setup):
    engine, tmp = setup
    result = _call(engine, filename=str(tmp / "x"), source_url="http://example.com/x", mode="append")
    assert "cannot be combined with source_url" in result


def test_append_without_content_writes_nothing_and_spends_no_write(setup):
    engine, tmp = setup
    target = tmp / "x.txt"
    result = _call(engine, filename=str(target), mode="append")
    assert "needs content" in result and not target.exists()
    assert engine._turn_writes == 0


def test_parts_of_one_file_count_as_one_write(setup):
    engine, tmp = setup
    engine.MAX_WRITES_PER_TURN = 1
    target = tmp / "one.py"
    _call(engine, filename=str(target), content="1\n")
    for part in "234":
        assert _call(engine, filename=str(target), content=f"{part}\n", mode="append").startswith("Appended ")
    assert engine._turn_writes == 1
    # A second file is a second write and hits the limit.
    refused = _call(engine, filename=str(tmp / "two.py"), content="x")
    assert "max_writes_per_turn" in refused


def test_an_append_to_a_file_not_written_this_turn_is_a_new_write(setup):
    engine, tmp = setup
    engine.MAX_WRITES_PER_TURN = 1
    (tmp / "old.txt").write_text("existing\n", encoding="utf-8")
    assert _call(engine, filename=str(tmp / "other.txt"), content="x").startswith("Wrote ")
    refused = _call(engine, filename=str(tmp / "old.txt"), content="more\n", mode="append")
    assert "max_writes_per_turn" in refused
    assert (tmp / "old.txt").read_text(encoding="utf-8") == "existing\n"


def test_bytes_are_summed_across_the_parts(setup):
    engine, tmp = setup
    engine.MAX_WRITE_BYTES = 100
    target = tmp / "cap.txt"
    assert _call(engine, filename=str(target), content="a" * 60).startswith("Wrote ")
    refused = _call(engine, filename=str(target), content="b" * 60, mode="append")
    assert "max_write_bytes" in refused and "120" in refused
    assert target.read_text(encoding="utf-8") == "a" * 60
    assert _call(engine, filename=str(target), content="b" * 40, mode="append").startswith("Appended ")


def test_a_fresh_turn_starts_a_fresh_series(setup):
    engine, tmp = setup
    engine.MAX_WRITES_PER_TURN = 1
    target = tmp / "t.txt"
    _call(engine, filename=str(target), content="1")
    engine.reset_turn_counters()
    assert _call(engine, filename=str(target), content="2", mode="append").startswith("Appended ")
    assert engine._turn_writes == 1


# -- the plan auditor ---------------------------------------------------------

@pytest.fixture
def audited(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PLAN_AUDIT = True
    engine.PLAN_AUDIT_REVERT = True
    engine.reset_turn_counters()
    verdicts = {"next": "VERDICT: pass"}
    seen = []

    def fake_subagent(args, depth=0, **kw):
        seen.append(args)
        return verdicts["next"]

    engine._exec_subagent = fake_subagent
    return engine, tmp_path, seen, verdicts


def test_parts_are_audited_once_at_the_next_other_call(audited):
    engine, tmp, seen, _ = audited
    target = tmp / "doc.txt"
    _call(engine, filename=str(target), content="part 1\n")
    audits_after_first = len(seen)  # an ordinary write is audited as before
    _call(engine, filename=str(target), content="part 2\n", mode="append")
    _call(engine, filename=str(target), content="part 3\n", mode="append")
    assert len(seen) == audits_after_first, "parts must not be audited one by one"
    engine.exec_tool("read_text", json.dumps({"filename": str(target)}))
    assert len(seen) == audits_after_first + 1
    assert "2 parts" in seen[-1]["task"]


def test_a_failed_audit_restores_the_file_from_before_the_first_part(audited):
    engine, tmp, seen, verdicts = audited
    target = tmp / "orig.txt"
    target.write_text("ORIGINAL\n", encoding="utf-8")
    _call(engine, filename=str(target), content="part 1\n")
    _call(engine, filename=str(target), content="part 2\n", mode="append")
    verdicts["next"] = "VERDICT: fail - the file is not what was asked"
    result = engine.exec_tool("read_text", json.dumps({"filename": str(target)}))
    assert "verification failed" in result
    assert target.read_text(encoding="utf-8") == "ORIGINAL\n"


def test_the_model_finishing_triggers_the_audit_of_pending_parts(audited):
    engine, tmp, seen, verdicts = audited
    target = tmp / "end.txt"
    target.write_text("ORIGINAL\n", encoding="utf-8")
    _call(engine, filename=str(target), content="a\n", mode="append")
    _call(engine, filename=str(target), content="b\n", mode="append")
    assert not seen
    verdicts["next"] = "VERDICT: fail - wrong"
    note = engine._flush_chunk_audits(0)
    assert "verification failed" in note and "Audit of" in note
    assert target.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert engine._flush_chunk_audits(0) == "", "an audited series is not audited twice"


def test_appending_to_another_file_flushes_the_first(audited):
    engine, tmp, seen, _ = audited
    a, b = tmp / "a.txt", tmp / "b.txt"
    _call(engine, filename=str(a), content="1", mode="append")
    assert not seen
    _call(engine, filename=str(b), content="1", mode="append")
    assert len(seen) == 1 and "a.txt" in seen[0]["task"]


def test_without_the_auditor_nothing_is_tracked(setup):
    engine, tmp = setup
    _call(engine, filename=str(tmp / "n.txt"), content="1", mode="append")
    assert engine._pending_chunk_audits == {} and engine._chunk_snapshots == {}


# -- the messages that lead the model here ---------------------------------------

def test_the_parse_error_for_write_file_points_to_append(engine):
    message = engine._tool_arg_parse_error("write_file", '{"filename": "a", "content": "x"y"}')
    assert 'mode="append"' in message and "4-6 KB" in message
    assert 'mode="append"' not in engine._tool_arg_parse_error("execute_shell", "{bad")


def test_a_cut_off_write_file_call_is_told_about_append(monkeypatch, engine):
    from types import SimpleNamespace as N
    partial = '✿FUNCTION✿: write_file ✿ARGS✿: {"filename": "a", "content": "xxxx'
    seen = []

    def fake(messages, tools, max_tokens=None, **kw):
        seen.append([m for m in messages if m["role"] == "user"][-1]["content"])
        if len(seen) == 1:
            return N(choices=[N(message=N(content=partial), finish_reason="length")])
        return N(choices=[N(message=N(content="ok"), finish_reason="stop")])

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake)
    engine.run_agent([{"role": "user", "content": "go"}], max_turns=4)
    assert 'mode="append"' in seen[1]
