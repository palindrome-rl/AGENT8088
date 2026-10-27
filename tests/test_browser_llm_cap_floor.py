"""The browsing model's completion cap no longer decays to 1024.

Seen in runs of a 27B reasoning model (qwen3.8-27b): about a third of the browsing sub-agent's calls were cut off by the length
limit, and most of those at decayed caps (4096, 3072, 2304, 1728, 1297, 1024 ...). A few short steps pulled the cap down, then a
step that thought for longer was cut; the browsing library reports an empty action, retries the whole step, and after five
failures ends the sub-agent. The floor is now 2048 (half the starting cap) and the cap shrinks toward 2.5x what a finished call
used, not 1.5x. A model that sets its own floor and starting cap behaves as before."""
from agent8088.browser_llm import Agent8088ChatModel


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
