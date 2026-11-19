"""A paragraph that states several requirements builds a checklist, and a part that asks
for something to be built counts as done only when the delivered files show it."""
import json
from types import SimpleNamespace as N

import pytest

from agent8088 import completion as c

PARAGRAPH = ("Build a small list tool. It should let users add items. It should let users remove "
             "items. It should be able to export the list. The page should look clean.")


# --- the checklist ----------------------------------------------------------------------

def test_a_paragraph_with_three_requirement_sentences_builds_a_checklist():
    items = c.extract_subgoals(PARAGRAPH)
    assert len(items) == 4
    assert items[0] == "It should let users add items"


def test_the_sentence_checklist_can_be_turned_off():
    assert c.extract_subgoals(PARAGRAPH, sentences=False) == []


@pytest.mark.parametrize("request_text", [
    "Build a small list tool. It should let users add items. It should let users remove items.",
    "What is the capital of France? It should be short.",
    "Fix the failing test in test_parser.py. It must pass.",
    "Summarize this document in three sentences.",
])
def test_fewer_than_three_requirements_build_no_checklist(request_text):
    assert c.extract_subgoals(request_text) == []


def test_a_numbered_list_is_unchanged():
    request = "Build a CLI:\n1. parse the arguments\n2. read the input file\n3. print a report"
    assert c.extract_subgoals(request) == ["parse the arguments", "read the input file", "print a report"]


# --- evidence in the delivered files ---------------------------------------------------------

def test_a_part_claimed_in_the_reply_but_absent_from_the_files_stays_open():
    items = ["It should be able to export the list", "It should let users remove items"]
    files = "def add(item): ...\ndef remove_item(item): ..."
    reply = "It is able to export the list, and users can remove items."
    assert c.unaddressed_subgoals(items, reply, ["tool.py"], evidence=files) == [items[0]]
    # the old reply-only check would have accepted both
    assert c.unaddressed_subgoals(items, reply, ["tool.py"]) == []


def test_camel_case_and_inflections_count_as_evidence():
    items = ["Players should get random teams", "Users must be able to remove items"]
    files = "function randomizeTeam(player) {}  // removing items for users from the list"
    assert c.unaddressed_subgoals(items, "", ["a.js"], evidence=files) == []


def test_a_part_that_only_asks_for_an_explanation_is_met_by_the_reply():
    items = ["explain the tradeoffs of the design"]
    assert c.unaddressed_subgoals(items, "The main tradeoffs of the design are speed and size.",
                                  ["a.py"], evidence="def f(): pass") == []
    assert c.unaddressed_subgoals(items, "Done.", ["a.py"], evidence="def f(): pass") == items


def test_without_readable_files_the_reply_is_still_the_evidence():
    items = ["summarize the sales figures"]
    assert c.unaddressed_subgoals(items, "Sales figures summary: revenue grew.", [], evidence=None) == []


def test_deliverable_text_reads_text_files_and_skips_the_rest(tmp_path):
    (tmp_path / "app.js").write_text("function addItem() {}", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG not text")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("SHOULD NOT APPEAR", encoding="utf-8")
    text = c.deliverable_text(["app.js", "logo.png", "node_modules/dep.js", "missing.txt"], [tmp_path])
    assert "addItem" in text and "SHOULD NOT APPEAR" not in text and "PNG" not in text


def test_deliverable_text_is_capped(tmp_path):
    (tmp_path / "big.txt").write_text("x" * 5000, encoding="utf-8")
    assert len(c.deliverable_text(["big.txt"], [tmp_path], per_file=1000, total=800)) <= 800


# --- through the agent loop -----------------------------------------------------------------

def _reply(content):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="stop")])


def _write(path, content, mode=None):
    args = {"filename": str(path), "content": content}
    if mode:
        args["mode"] = mode
    return "✿FUNCTION✿: write_file ✿ARGS✿: " + json.dumps(args)


@pytest.fixture
def eng(engine, tmp_path, monkeypatch):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path
    engine.PLAN_AUDIT = False
    engine.reset_turn_counters()
    monkeypatch.setattr(engine, "REQUIREMENT_EVIDENCE", True)
    monkeypatch.setattr(engine, "COMPLETION_SMOKE_TEST", "off")
    return engine


def _run(engine, monkeypatch, script):
    sent = []

    def model(messages, tools, **kwargs):
        sent.append(str(messages[-1].get("content")))
        return _reply(script(len(sent) - 1))

    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)
    answer = engine.run_agent([{"role": "user", "content": PARAGRAPH}], max_turns=12,
                              dynamic_turns=False)
    return answer, sent


def test_a_reply_that_claims_a_part_the_files_lack_is_sent_back(eng, monkeypatch, tmp_path):
    target = tmp_path / "list_tool.py"

    def script(i):
        return [
            _write(target, "items = []\ndef add_item(x): items.append(x)\ndef remove_item(x): items.remove(x)\n"
                           "# page styles: clean layout\n"),
            "All done: adding, removing and export are implemented.",
            _write(target, "def export_list(path): open(path, 'w').write(str(items))\n", mode="append"),
            "Done, export added.",
        ][min(i, 3)]

    answer, sent = _run(eng, monkeypatch, script)
    assert any("do not show up in anything you wrote" in s and "export" in s for s in sent)
    assert "export_list" in target.read_text(encoding="utf-8")
    assert "Not completed" not in answer


def test_with_the_check_off_the_reply_is_accepted(eng, monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "REQUIREMENT_EVIDENCE", False)
    target = tmp_path / "list_tool.py"

    def script(i):
        return [_write(target, "items = []\n"), "All done: add, remove and export are implemented."][min(i, 1)]

    answer, sent = _run(eng, monkeypatch, script)
    assert not any("do not show up" in s for s in sent)
