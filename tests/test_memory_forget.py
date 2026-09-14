"""memory.forget() -- the model-facing counterpart to /memory forget.

Deliberately conservative: it deletes only when the search narrows to exactly
one memory. Zero hits and multiple hits both leave the store untouched, so a
vague query can never sweep more than the one thing it clearly names.
"""
import pytest

from agent8088 import memory


@pytest.fixture(autouse=True)
def _reset_memory():
    yield
    memory.reset()


def _configure(tmp_path, **overrides):
    config = {"memory": "1", "memory_engine": "native", "memory_capture": "1"}
    config.update(overrides)
    memory.configure(config=config, db_path=str(tmp_path / "memory.sqlite3"))


def test_forget_deletes_the_one_clear_match(tmp_path):
    _configure(tmp_path)
    memory.store().add("the user's facebook username is tayyab2132",
                       user_id="owner", source="user")

    result = memory.forget("facebook username")

    assert "tayyab2132" in result
    assert memory.recall("facebook username", identity="owner") == []


def test_forget_reports_no_match_without_touching_anything(tmp_path):
    _configure(tmp_path)
    memory.store().add("the user prefers uv over pip", user_id="owner", source="user")

    result = memory.forget("their shoe size")

    assert "no memory" in result.lower()
    assert memory.store().count(user_id="owner") == 1


def test_forget_refuses_to_guess_between_several_matches(tmp_path):
    _configure(tmp_path)
    store = memory.store()
    store.add("the user's facebook username is tayyab2132", user_id="owner", source="user")
    store.add("the user's twitter username is tayyabi", user_id="owner", source="user")
    store.add("the user's instagram username is tayyab.im", user_id="owner", source="user")

    result = memory.forget("username")

    # Nothing deleted -- ambiguity must never resolve to a guess.
    assert store.count(user_id="owner") == 3
    assert "matches" in result.lower()


def test_forget_reports_when_memory_is_off(tmp_path):
    _configure(tmp_path, memory="0")
    assert "off" in memory.forget("anything").lower()


def test_forget_rejects_an_empty_query(tmp_path):
    _configure(tmp_path)
    result = memory.forget("   ")
    assert "no query" in result.lower() or "describe" in result.lower()
