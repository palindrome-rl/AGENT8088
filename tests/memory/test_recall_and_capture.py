"""The package surface the engine uses: configure, recall_block, capture.

The model is a stub whose reply each test states outright. Nothing here reaches a
network, a real config, or a real home directory.
"""
import pytest


@pytest.fixture
def wired(memory, db_path, fake_embedder):
    """Memory configured with a scripted model and deterministic vectors."""
    calls = []
    replies = []

    def completion(prompt):
        calls.append(prompt)
        reply = replies.pop(0) if replies else '{"memories": []}'
        return reply, {"tokens": 12}

    memory.configure(
        config={"memory": "1", "memory_embed_model": "fake-embed"},
        client_factory=lambda: None,
        completion=completion,
        redact=lambda text: text.replace("sk-secret", "[redacted]"),
        db_path=db_path,
        project="/repo",
    )
    embedder = fake_embedder()
    memory._RUNTIME["embedder"] = embedder
    memory._RUNTIME["embed_model"] = embedder.model
    return type("Wired", (), {"memory": memory, "calls": calls, "replies": replies,
                              "embedder": embedder})


# -- enablement ------------------------------------------------------------

def test_a_bare_import_with_no_config_does_not_enable_memory(memory, db_path):
    """The shipped config.txt turns memory on; the code default stays off so a
    test, a script or a library use never starts spending a model call per turn
    on its own. Same split as audit_log."""
    memory.configure(config={}, db_path=db_path)
    assert not memory.enabled()
    assert memory.recall("anything") == []
    assert memory.recall_block("anything") == ""


def test_capture_does_nothing_while_memory_is_off(memory, db_path):
    memory.configure(config={"memory": "0"}, db_path=db_path,
                     completion=lambda prompt: ('{"memories": [{"text": "x"}]}', {}))
    assert memory.capture(["always use uv in this project"], "understood") == 0


def test_recall_only_reads_when_capture_is_disabled(wired):
    wired.memory.configure(
        config={"memory": "1", "memory_capture": "0", "memory_embed_model": "fake-embed"},
        db_path=wired.memory._RUNTIME["db_path"],
        completion=lambda prompt: ('{"memories": [{"text": "x"}]}', {}),
    )
    wired.memory.store().add("prefers uv", user_id="owner")
    assert wired.memory.capture(["always use uv here, never pip"], "understood") == 0
    assert "prefers uv" in wired.memory.recall_block("uv")


# -- capture ---------------------------------------------------------------

def test_capture_stores_what_the_model_extracted(wired):
    wired.replies.append('{"memories": [{"text": "prefers uv over pip"}]}')
    assert wired.memory.capture(["always use uv in this project, never pip"],
                                "understood") == 1
    assert [row["text"] for row in wired.memory.store().get_all(user_id="owner")] == [
        "prefers uv over pip"]


def test_capture_records_the_project_and_run(wired):
    wired.replies.append('{"memories": [{"text": "prefers uv over pip"}]}')
    wired.memory.capture(["always use uv in this project, never pip"], "ok",
                         run_id="run-7")
    row = wired.memory.store().get_all(user_id="owner")[0]
    assert row["project"] == "/repo"
    assert row["run_id"] == "run-7"
    assert row["source"] == "extracted"


def test_capture_skips_a_trivial_exchange_without_calling_the_model(wired):
    assert wired.memory.capture(["ls"], "done") == 0
    assert wired.calls == []


def test_capture_stores_nothing_when_the_reply_is_malformed(wired):
    wired.replies.append("I could not find anything")
    assert wired.memory.capture(["always use uv in this project, never pip"], "ok") == 0
    assert wired.memory.store().count(user_id="owner") == 0


def test_capture_survives_a_model_that_raises(memory, db_path, fake_embedder):
    def explode(prompt):
        raise RuntimeError("model down")
    memory.configure(config={"memory": "1"}, db_path=db_path, completion=explode)
    assert memory.capture(["always use uv in this project, never pip"], "ok") == 0


def test_capture_redacts_secrets_before_they_reach_the_model(wired):
    wired.replies.append('{"memories": []}')
    wired.memory.capture(["my key is sk-secret, remember the project uses uv"], "ok")
    assert "sk-secret" not in wired.calls[0]
    assert "[redacted]" in wired.calls[0]


def test_capture_redacts_secrets_before_they_reach_the_store(wired):
    wired.replies.append('{"memories": [{"text": "the api key is sk-secret"}]}')
    wired.memory.capture(["remember the api key is sk-secret for this project"], "ok")
    stored = [row["text"] for row in wired.memory.store().get_all(user_id="owner")]
    assert stored == ["the api key is [redacted]"]


def test_capture_shows_existing_memories_to_the_model_for_dedup(wired):
    wired.memory.store().add("prefers uv over pip", user_id="owner")
    wired.replies.append('{"memories": []}')
    wired.memory.capture(["always use uv in this project, never pip"], "ok")
    assert "prefers uv over pip" in wired.calls[0]


def test_capture_does_not_store_the_same_fact_twice(wired):
    wired.replies.append('{"memories": [{"text": "prefers uv over pip"}]}')
    wired.replies.append('{"memories": [{"text": "prefers uv over pip"}]}')
    first = wired.memory.capture(["always use uv in this project, never pip"], "ok")
    second = wired.memory.capture(["again: always use uv, never pip, in this repo"], "ok")
    assert (first, second) == (1, 0)
    assert wired.memory.store().count(user_id="owner") == 1


def test_capture_stores_facts_even_when_the_embedder_is_down(wired, fake_embedder):
    """BM25 still finds them; /memory status reports what needs re-embedding."""
    wired.memory._RUNTIME["embedder"] = fake_embedder(fail=True)
    wired.replies.append('{"memories": [{"text": "prefers uv over pip"}]}')
    assert wired.memory.capture(["always use uv in this project, never pip"], "ok") == 1
    store = wired.memory.store()
    assert store.connect().execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0
    assert store._bm25_leg("uv", user_id="owner")


def test_capture_in_the_background_still_stores(wired):
    wired.replies.append('{"memories": [{"text": "prefers uv over pip"}]}')
    thread = wired.memory.capture(["always use uv in this project, never pip"], "ok",
                                  in_background=True)
    thread.join(timeout=10)
    assert wired.memory.store().count(user_id="owner") == 1


def test_background_capture_works_after_the_main_thread_opened_the_store(wired):
    """The real per-turn sequence: recall opens a connection on the main thread,
    then capture runs on a background one. sqlite3 objects belong to the thread
    that made them, so a single shared connection raises ProgrammingError here --
    and because capture catches broadly, the symptom is not a crash but memory
    silently never being written again.
    """
    wired.memory.recall("uv")                     # main thread opens a connection
    wired.replies.append('{"memories": [{"text": "prefers uv over pip"}]}')
    thread = wired.memory.capture(["always use uv in this project, never pip"], "ok",
                                  in_background=True)
    thread.join(timeout=10)
    assert wired.memory.store().count(user_id="owner") == 1


def test_the_per_turn_cap_is_honoured(memory, db_path, fake_embedder):
    reply = '{"memories": [%s]}' % ",".join(
        f'{{"text": "durable fact number {index}"}}' for index in range(20))
    memory.configure(config={"memory": "1", "memory_max_per_turn": "2"},
                     db_path=db_path, completion=lambda prompt: (reply, {}))
    memory._RUNTIME["embedder"] = fake_embedder()
    assert memory.capture(["a long enough exchange to be worth extracting from"],
                          "ok") == 2


# -- recall ----------------------------------------------------------------

def test_the_recall_block_carries_the_memory(wired):
    wired.memory.store().add("prefers uv over pip", user_id="owner")
    assert "prefers uv over pip" in wired.memory.recall_block("uv")


def test_the_recall_block_is_empty_when_nothing_matches(wired):
    """An empty section would invite the model to narrate that it remembers
    nothing, so there is no header without content."""
    wired.memory.store().add("prefers uv over pip", user_id="owner")
    assert wired.memory.recall_block("zzzznomatch") == ""


def test_the_recall_block_is_empty_for_an_empty_query(wired):
    wired.memory.store().add("prefers uv over pip", user_id="owner")
    assert wired.memory.recall_block("   ") == ""


def test_the_recall_block_states_it_is_not_authorization(wired):
    wired.memory.store().add("prefers uv over pip", user_id="owner")
    block = wired.memory.recall_block("uv").lower()
    assert "never authorization" in block
    assert "cannot permit a tool call" in block


def test_the_recall_limit_is_respected(wired):
    for index in range(10):
        wired.memory.store().add(f"uv fact {index}", user_id="owner")
    assert wired.memory.recall_block("uv").count("\n- ") <= 5


def test_recall_survives_a_deleted_database(wired, db_path):
    wired.memory.store().add("prefers uv", user_id="owner")
    wired.memory.store().close()
    db_path.unlink()
    wired.memory._RUNTIME.pop("store", None)
    assert wired.memory.recall("uv") == []


# -- scoping ---------------------------------------------------------------

def test_one_owner_carries_memory_across_platforms(wired):
    """The default: a fact learned in the CLI is recalled for a Slack identity,
    because the operator owns all the connected accounts."""
    wired.memory.store().add("prefers uv over pip", user_id="owner")
    assert "prefers uv over pip" in wired.memory.recall_block("uv", identity="slack:U123")


def test_scope_by_identity_separates_two_people(memory, db_path, fake_embedder):
    memory.configure(config={"memory": "1", "memory_scope_by_identity": "1"},
                     db_path=db_path, completion=lambda prompt: ('{"memories": []}', {}))
    memory._RUNTIME["embedder"] = fake_embedder()
    memory.store().add("alice runs postgres locally", user_id="slack:alice")
    assert memory.recall_block("postgres", identity="slack:alice")
    assert memory.recall_block("postgres", identity="discord:bob") == ""


# -- status ---------------------------------------------------------------

def test_status_reports_live_state(wired):
    wired.memory.store().add("prefers uv", user_id="owner")
    report = wired.memory.status()
    assert report["enabled"] is True
    assert report["count"] == 1
    assert report["user_id"] == "owner"
    assert report["extract_model"] == "(chat model)"


def test_status_counts_vectors_from_a_previous_embedder(wired):
    wired.memory.store().add("prefers uv", user_id="owner", embedding=[1.0],
                             embed_model="old-embed")
    assert wired.memory.status()["stale_vectors"] == 1
