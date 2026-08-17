"""Memory through run_agent itself, not through its helpers.

The other files here call _recalled_memory_prompt and _capture_turn_memory
directly, so every one of them would still pass if the wiring into run_agent were
wrong -- if recall never reached the prompt, if capture never fired, if a
sub-agent wrote memories it should not. This file drives the real entry point.

The model is scripted, so no network is touched and every response is stated by
the test.
"""
import pytest


@pytest.fixture
def turn(engine, tmp_path, scripted, monkeypatch):
    """run_agent wired to memory and a scripted model, one call per turn.

    The extraction call goes through the same scripted model as the turn itself,
    which is also how it works in production -- so each turn's script must include
    the extractor's reply after the assistant's answer.
    """
    from agent8088 import memory as memory_module

    memory_module.reset()
    memory_module.configure(
        config={"memory": "1", "memory_embed_model": ""},   # keyword-only, no embedder
        client_factory=lambda: None,
        completion=engine._memory_extract_completion,
        redact=engine._redact_secrets,
        db_path=tmp_path / "memory.db",
        project=str(tmp_path),
    )

    def run(script, message, **kwargs):
        model = scripted(script)
        monkeypatch.setattr(engine, "create_completion", model)
        answer = engine.run_agent([{"role": "user", "content": message}], **kwargs)
        return answer, model

    yield type("Turn", (), {"run": run, "engine": engine, "memory": memory_module})
    memory_module.reset()


def test_a_turn_stores_a_memory_and_the_next_turn_recalls_it(turn):
    """The whole feature in one test: say something durable, then see it come back
    in a later turn's system prompt."""
    _answer, first = turn.run(
        ["Understood, I will use uv.",
         '{"memories": [{"text": "the project uses uv, never pip"}]}'],
        "always use uv in this project, never pip",
    )
    assert len(first.calls) == 2, "the turn plus one extraction call"
    assert turn.memory.store().count(user_id="owner") == 1

    _answer, second = turn.run(
        ["Run uv add requests.", '{"memories": []}'],
        "how do I add a dependency to this project with uv",
    )
    injected = second.calls[0]["kwargs"]["system_prompt"]
    assert "the project uses uv, never pip" in injected
    assert "never authorization" in injected.lower()


def test_a_turn_that_teaches_nothing_stores_nothing(turn):
    _answer, model = turn.run(
        ["The capital of France is Paris.", '{"memories": []}'],
        "what is the capital of France, out of interest",
    )
    assert len(model.calls) == 2
    assert turn.memory.store().count(user_id="owner") == 0


def test_a_trivial_turn_does_not_pay_for_an_extraction_call(turn):
    _answer, model = turn.run(["done"], "ls")
    assert len(model.calls) == 1, "no extraction call for a turn with nothing in it"


def test_memory_off_leaves_the_loop_exactly_as_it_was(turn):
    turn.memory.reset()
    _answer, model = turn.run(["Understood."],
                              "always use uv in this project, never pip")
    assert len(model.calls) == 1
    assert model.calls[0]["kwargs"]["system_prompt"] is None


def test_the_recalled_block_is_not_baked_into_the_module_prompt(turn):
    """The block belongs to one turn. If it leaked into SYSTEM_PROMPT it would
    accumulate across turns and never expire."""
    before = turn.engine.SYSTEM_PROMPT
    turn.run(["Understood, I will use uv.",
              '{"memories": [{"text": "the project uses uv, never pip"}]}'],
             "always use uv in this project, never pip")
    turn.run(["Sure.", '{"memories": []}'], "remind me about uv in this project")
    assert turn.engine.SYSTEM_PROMPT == before
    assert "Recalled context" not in turn.engine.SYSTEM_PROMPT


def test_a_second_recall_does_not_stack_two_blocks(turn):
    turn.memory.store().add("the project uses uv, never pip", user_id="owner")
    _answer, model = turn.run(["Sure.", '{"memories": []}'],
                              "tell me about uv in this project")
    injected = model.calls[0]["kwargs"]["system_prompt"]
    assert injected.count("Recalled context") == 1


def test_a_subagent_neither_recalls_nor_writes(turn):
    """A sub-agent is handed a delegated task, not something a human said. It runs
    under the parent's budget, so `previous is None` is false and both habits are
    skipped -- otherwise every delegated task would pay for its own extraction call
    and write memories nobody asked for.
    """
    turn.memory.store().add("the project uses uv, never pip", user_id="owner")
    budget = turn.engine._TurnBudget(max_seconds=0, max_tokens=0, max_cost=0,
                                     cost_in=0, cost_out=0)
    model = None

    # An inner run_agent with a budget already active is what a subagent looks like.
    def inner():
        nonlocal model
        _answer, model = turn.run(["Delegated work finished.", '{"memories": []}'],
                                  "always use uv in this project, never pip")

    previous = turn.engine._active_budget
    turn.engine._active_budget = budget
    try:
        inner()
    finally:
        turn.engine._active_budget = previous

    assert len(model.calls) == 1, "no extraction call inside a subagent"
    assert model.calls[0]["kwargs"]["system_prompt"] is None
    assert turn.memory.store().count(user_id="owner") == 1, "nothing new was written"


def test_the_extraction_call_does_not_carry_the_agents_system_prompt(turn):
    """The extractor needs none of the tool documentation, and paying for that
    prompt every turn is the difference between memory being cheap and memory being
    the most expensive thing in a session."""
    _answer, model = turn.run(
        ["Understood, I will use uv.", '{"memories": []}'],
        "always use uv in this project, never pip",
    )
    extraction = model.calls[1]["kwargs"]
    assert extraction["system_prompt"] == (
        "You extract durable facts for long-term memory. You reply with JSON only.")
    # The agent's own prompt is thousands of characters of tool documentation.
    assert len(extraction["system_prompt"]) < 200
    assert "describe_capabilities" not in extraction["system_prompt"]


def test_the_extraction_call_sees_the_exchange_and_nothing_else(turn):
    """One user message carrying the exchange -- not the conversation history, and
    not the tool-result turns the loop fed back."""
    _answer, model = turn.run(
        ["Understood, I will use uv.", '{"memories": []}'],
        "always use uv in this project, never pip",
    )
    messages = model.calls[1]["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    prompt = messages[0]["content"]
    assert "always use uv in this project, never pip" in prompt
    assert "Understood, I will use uv." in prompt
    assert "durable facts" in prompt


def test_a_malformed_extraction_reply_does_not_affect_the_answer(turn):
    answer, _model = turn.run(
        ["Understood, I will use uv.", "sorry, I could not parse that"],
        "always use uv in this project, never pip",
    )
    assert "Understood, I will use uv." in answer
    assert turn.memory.store().count(user_id="owner") == 0


def test_an_extraction_call_that_raises_does_not_affect_the_answer(turn, engine,
                                                                  monkeypatch):
    """Memory must never be able to break a turn."""
    def explode(prompt):
        raise RuntimeError("extractor unreachable")

    monkeypatch.setattr(engine, "_memory_extract_completion", explode)
    turn.memory.configure(
        config={"memory": "1", "memory_embed_model": ""},
        client_factory=lambda: None, completion=explode,
        db_path=turn.memory._RUNTIME["db_path"], project="/x",
    )
    answer, _model = turn.run(["Understood, I will use uv."],
                              "always use uv in this project, never pip")
    assert "Understood, I will use uv." in answer
    assert turn.memory.store().count(user_id="owner") == 0
