"""The browsing model's completion cap no longer decays to 1024.

Seen in runs of a 27B reasoning model (qwen3.8-27b): about a third of the browsing sub-agent's calls were cut off by the length
limit, and most of those at decayed caps (4096, 3072, 2304, 1728, 1297, 1024 ...). A few short steps pulled the cap down, then a
step that thought for longer was cut; the browsing library reports an empty action, retries the whole step, and after five
failures ends the sub-agent. The floor is now 2048 (half the starting cap) and the cap shrinks toward 2.5x what a finished call
used, not 1.5x. A model that sets its own floor and starting cap behaves as before."""
from agent8088.browser_llm import Agent8088ChatModel
import pytest


def _model(**kw):
    return Agent8088ChatModel(model="openai/gpt-4o", max_tokens=4096, **kw)


def _old():
    """The previous behaviour: floor 1024, shrink toward 1.5x."""
    return _model(min_completion_tokens=1024, usage_headroom=1.5)


def test_the_defaults():
    model = _model()
    assert model.min_completion_tokens == 2048
    assert model.usage_headroom == 2.5


def test_short_steps_no_longer_decay_the_cap_to_the_old_floor():
    model = _model()
    for _ in range(40):
        model._record_completion_usage("stop", 300)
    assert model._adaptive_cap == 2048


def test_a_long_thinking_step_after_short_ones_now_fits():
    """The same sequence of replies: the old settings leave a cap too small for a 1900-token step, the new ones do not."""
    new, old = _model(), _old()
    for tokens in (500, 550, 600, 520, 580, 560, 540, 610, 590, 570, 530, 600, 560):
        new._record_completion_usage("stop", tokens)
        old._record_completion_usage("stop", tokens)
    assert old._completion_cap() < 1900
    assert new._completion_cap() >= 1900


def test_a_cut_off_still_doubles_the_cap_up_to_the_ceiling():
    model = _model(max_completion_tokens=10000)
    model._record_completion_usage("length", 4096)
    assert model._adaptive_cap == 8192
    model._record_completion_usage("length", 8192)
    assert model._adaptive_cap == 10000


def test_a_cut_that_did_not_reach_the_cap_is_not_a_cap_problem():
    model = _model()
    model._record_completion_usage("length", 500)      # the provider cut it, not our cap
    assert model._adaptive_cap <= 4096


def test_an_explicit_floor_still_wins():
    model = _model(min_completion_tokens=1024)
    for _ in range(40):
        model._record_completion_usage("stop", 100)
    assert model._adaptive_cap == 1024


def test_a_starting_cap_below_the_floor_starts_at_the_floor():
    model = Agent8088ChatModel(model="openai/gpt-4o", max_tokens=1500)
    assert model._adaptive_cap == 2048


def test_the_engine_default_matches(engine):
    assert engine.BROWSER_LLM_MIN_TOKENS == 2048


@pytest.mark.parametrize('start, floor, ceiling', [(1024, 2048, 1024), (4096, 2048, 1024),
                                                 (50000, 2048, 6000), (1, 8192, 256)])
def test_the_ceiling_wins_on_initial_and_later_requests(start, floor, ceiling):
    model = Agent8088ChatModel(model='openai/gpt-4o', max_tokens=start,
                              min_completion_tokens=floor, max_completion_tokens=ceiling)
    assert 1 <= model._completion_cap() <= ceiling
    for reason, tokens in [('length', ceiling), ('stop', 100), ('length', ceiling), ('stop', 0)]:
        model._record_completion_usage(reason, tokens)
        assert 1 <= model._completion_cap() <= ceiling


def test_an_unset_starting_cap_remains_unset():
    assert Agent8088ChatModel(model='openai/gpt-4o', max_tokens=None)._completion_cap() is None


@pytest.mark.parametrize('setting', ['min_completion_tokens', 'max_completion_tokens'])
def test_invalid_bounds_fail_before_a_provider_request(setting):
    with pytest.raises(ValueError, match='must be positive'):
        _model(**{setting: 0})


@pytest.mark.asyncio
async def test_actual_provider_arguments_respect_a_small_ceiling(monkeypatch):
    from types import SimpleNamespace
    from pydantic import BaseModel

    class Output(BaseModel):
        answer: str

    calls = []
    async def complete(**kwargs):
        calls.append(kwargs['max_tokens'])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"answer":"ok"}', reasoning_content=None),
            finish_reason='length')], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1024))
    monkeypatch.setattr('litellm.acompletion', complete)
    model = Agent8088ChatModel(model='openai/gpt-4o', max_tokens=1024,
                              max_completion_tokens=1024, extra_body={'fixture': True})
    await model.ainvoke([], output_format=Output)
    await model.ainvoke([], output_format=Output)
    assert calls == [1024, 1024]
