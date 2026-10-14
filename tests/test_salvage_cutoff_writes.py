"""A text write_file call cut off mid-content is saved up to its last whole line when that is
safe, the file is marked unfinished, and the retry asks for the rest as an append.
"""
import json

import pytest

from test_cutoff_recovery import Model, _reply

LINE = "const row_{:03d} = {{ name: 'entry {:03d}', value: {} }};\n"


def _body(n=40):
    return "".join(LINE.format(i, i, i) for i in range(n))


def _cut_call(path, content, mode=None, cut_mid_line=True):
    """What a reply looks like when the token limit hit inside the content string."""
    text = content + ("const half = {name: 'ha" if cut_mid_line else "")
    escaped = json.dumps(text)[1:-1]
    head = {"filename": str(path)}
    if mode:
        head["mode"] = mode
    return ("✿FUNCTION✿: write_file ✿ARGS✿: " + json.dumps(head)[:-1] + ', "content": "' + escaped)


@pytest.fixture
def setup(engine, tmp_path, monkeypatch):
    real = engine.exec_tool
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PLAN_AUDIT = False
    engine.reset_turn_counters()
    monkeypatch.setattr(engine, "SALVAGE_CUTOFF_WRITES", True, raising=False)

    def run(replies, trace=None, max_turns=8):
        model = Model(engine, monkeypatch, replies)
        monkeypatch.setattr(engine, "exec_tool", real)
        answer = engine.run_agent([{"role": "user", "content": "build the app"}], max_turns=max_turns,
                                  tools_def=[], allowed_tools={"write_file", "read_text"}, trace=trace)
        return model, answer

    return engine, tmp_path, run


def test_a_cutoff_new_file_is_saved_to_its_last_whole_line(setup):
    engine, tmp, run = setup
    target = tmp / "data.js"
    body = _body()
    trace = []
    model, _ = run([_reply(_cut_call(target, body), "length"), _reply("done")], trace=trace)
    assert target.read_text(encoding="utf-8") == body, "the half line is dropped, whole lines are kept"
    assert any(t.get("type") == "cutoff_write_salvaged" for t in trace)
    retry = model.sent[1]
    assert 'mode="append"' in retry and "starting right after that last line" in retry
    assert "const row_039" in retry, "the model is shown how the saved file ends"


def test_the_file_is_unfinished_until_it_is_continued(setup):
    engine, tmp, run = setup
    target = tmp / "data.js"
    body = _body()
    model, answer = run([
        _reply(_cut_call(target, body), "length"),
        _reply("All finished."),                    # tries to stop with the file cut short
        _reply('✿FUNCTION✿: write_file ✿ARGS✿: ' + json.dumps(
            {"filename": str(target), "content": "// end\n", "mode": "append"})),
        _reply("Done, the file is complete.")])
    assert any("cut off while being written" in s for s in model.sent), "the completion check sends it back"
    assert target.read_text(encoding="utf-8").endswith("// end\n")
    assert answer.startswith("Done")


def test_an_existing_file_is_never_overwritten_by_a_salvage(setup):
    engine, tmp, run = setup
    target = tmp / "data.js"
    target.write_text("original\n", encoding="utf-8")
    trace = []
    run([_reply(_cut_call(target, _body()), "length"), _reply("done")], trace=trace)
    assert target.read_text(encoding="utf-8") == "original\n"
    assert not any(t.get("type") == "cutoff_write_salvaged" for t in trace)


def test_a_cutoff_append_part_is_added_to_the_file(setup):
    engine, tmp, run = setup
    target = tmp / "data.js"
    target.write_text("// part 1\n", encoding="utf-8")
    body = _body()
    run([_reply(_cut_call(target, body, mode="append"), "length"), _reply("done")])
    assert target.read_text(encoding="utf-8") == "// part 1\n" + body


def test_a_json_file_is_not_saved_part_way(setup):
    engine, tmp, run = setup
    target = tmp / "data.json"
    run([_reply(_cut_call(target, _body()), "length"), _reply("done")])
    assert not target.exists()


def test_a_short_partial_is_not_worth_saving(setup):
    engine, tmp, run = setup
    target = tmp / "small.js"
    run([_reply(_cut_call(target, _body(3)), "length"), _reply("done")])
    assert not target.exists()


def test_it_can_be_turned_off(setup, monkeypatch):
    engine, tmp, run = setup
    monkeypatch.setattr(engine, "SALVAGE_CUTOFF_WRITES", False, raising=False)
    target = tmp / "data.js"
    run([_reply(_cut_call(target, _body()), "length"), _reply("done")])
    assert not target.exists()


def test_once_per_file(setup):
    engine, tmp, run = setup
    target = tmp / "data.js"
    body = _body()
    trace = []
    run([_reply(_cut_call(target, body), "length"),
         _reply(_cut_call(target, body, mode="append"), "length"),
         _reply("done")], trace=trace)
    assert sum(1 for t in trace if t.get("type") == "cutoff_write_salvaged") == 1


def test_another_tool_call_cut_off_is_untouched(setup):
    engine, tmp, run = setup
    cut = '✿FUNCTION✿: execute_shell ✿ARGS✿: {"command": "echo ' + "x" * 1500
    model, _ = run([_reply(cut, "length"), _reply("done")])
    assert not list(tmp.iterdir())


def test_the_decoder_handles_escapes_and_a_half_escape():
    from agent8088 import engine
    value, closed = engine._decode_open_json_string('a\\nb\\"c\\u00e9\\')
    assert value == 'a\nb"cé' and not closed
    assert engine._decode_open_json_string('done" , "mode": "append"') == ("done", True)
