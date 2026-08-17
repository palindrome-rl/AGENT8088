"""Parsing a model's extraction reply, and refusing to guess when it is malformed."""
import pytest

from agent8088.memory import extract


def test_a_clean_reply_yields_its_memories():
    got = extract.parse_response('{"memories": [{"text": "prefers uv", "categories": ["tooling"]}]}')
    assert got == [{"text": "prefers uv", "categories": ["tooling"]}]


def test_a_fenced_reply_is_accepted():
    got = extract.parse_response('```json\n{"memories": [{"text": "prefers uv"}]}\n```')
    assert [item["text"] for item in got] == ["prefers uv"]


def test_a_reply_with_a_preamble_is_accepted():
    got = extract.parse_response('Sure! Here you go:\n{"memories": [{"text": "prefers uv"}]}')
    assert [item["text"] for item in got] == ["prefers uv"]


@pytest.mark.parametrize("reply", [
    "", "   ", "no durable facts here", "{{{", "[1,2,3]",
    '{"memories": "prefers uv"}', '{"notmemories": []}', "null",
])
def test_an_unparseable_reply_stores_nothing(reply):
    """No repair and no free-text fallback. A mangled fact would be recalled as
    truth for months, which is strictly worse than remembering nothing."""
    assert extract.parse_response(reply) == []


def test_an_empty_list_is_a_valid_answer():
    assert extract.parse_response('{"memories": []}') == []


def test_bare_strings_are_accepted_as_memories():
    got = extract.parse_response('{"memories": ["prefers uv", "no CI on this repo"]}')
    assert [item["text"] for item in got] == ["prefers uv", "no CI on this repo"]


def test_the_per_turn_cap_is_enforced():
    reply = '{"memories": [%s]}' % ",".join(
        f'{{"text": "fact number {index}"}}' for index in range(50))
    assert len(extract.parse_response(reply, max_memories=3)) == 3


def test_an_over_long_memory_is_truncated_not_dropped():
    reply = '{"memories": [{"text": "%s"}]}' % ("x" * 5000)
    assert len(extract.parse_response(reply)[0]["text"]) == extract.MAX_MEMORY_CHARS


def test_duplicates_within_one_reply_are_collapsed():
    got = extract.parse_response(
        '{"memories": [{"text": "prefers uv"}, {"text": "Prefers UV"}]}')
    assert len(got) == 1


def test_blank_memories_are_dropped():
    assert extract.parse_response('{"memories": [{"text": "  "}, {"text": "real"}]}') == [
        {"text": "real", "categories": []}]


def test_malformed_categories_do_not_break_a_valid_memory():
    got = extract.parse_response('{"memories": [{"text": "prefers uv", "categories": "tooling"}]}')
    assert got == [{"text": "prefers uv", "categories": []}]


def test_a_trivial_exchange_is_not_worth_a_model_call():
    assert not extract.worth_extracting("User: ls\n\nAssistant: done")
    assert not extract.worth_extracting("")


def test_a_substantial_exchange_is_worth_a_model_call():
    assert extract.worth_extracting(
        "User: always use uv in this project, never pip\n\nAssistant: understood")


def test_the_exchange_carries_only_user_and_assistant_text():
    rendered = extract.format_exchange(["use uv here"], "understood")
    assert rendered == "User: use uv here\n\nAssistant: understood"


def test_an_exchange_with_no_answer_still_renders():
    assert extract.format_exchange(["use uv here"], "") == "User: use uv here"


def test_the_prompt_shows_existing_memories_for_dedup():
    prompt = extract.build_prompt("User: hi", ["prefers uv"])
    assert "prefers uv" in prompt
    assert "do not repeat" in prompt.lower()


def test_the_prompt_survives_its_own_json_braces():
    """The template contains literal JSON, so str.format would raise on it."""
    prompt = extract.build_prompt("User: hi", [], max_memories=7)
    assert '{"memories": [{"text": "...", "categories": ["..."]}]}' in prompt
    assert "At most 7" in prompt


def test_the_prompt_forbids_recording_credentials():
    assert "credentials" in extract.build_prompt("User: hi", []).lower()
