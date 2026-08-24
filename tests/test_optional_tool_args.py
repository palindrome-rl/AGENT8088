"""Optional arguments in the tools.txt registry.

build_tools_def emitted `"required": list(spec["args"])`, so every declared
argument was mandatory. That blocked pagination on read_text (the model would
have had to pass offset/limit on every read) and it is also why schedule_task
demands `schedule` and `task` for `action=list`, which needs neither — the
model had to invent throwaway values to make a listing call type-check.

execute_plan rejects a step missing any required arg, so the subtraction has
to reach TOOL_REQUIRED_PARAMS too, not just the JSON schema sent to the model.
"""


def _spec(engine, **extra):
    return engine._build_spec("demo", extra, {}, "demo tool")


def test_optional_args_are_still_offered_to_the_model(engine):
    spec = _spec(engine, args="filename,offset,limit", optional="offset,limit")
    props = engine.build_tools_def({"demo": spec})[0]["function"]["parameters"]["properties"]
    assert set(props) == {"filename", "offset", "limit"}


def test_optional_args_are_not_required(engine):
    spec = _spec(engine, args="filename,offset,limit", optional="offset,limit")
    params = engine.build_tools_def({"demo": spec})[0]["function"]["parameters"]
    assert params["required"] == ["filename"]


def test_absent_optional_field_keeps_every_arg_required(engine):
    spec = _spec(engine, args="filename,content")
    params = engine.build_tools_def({"demo": spec})[0]["function"]["parameters"]
    assert params["required"] == ["filename", "content"]


def test_optional_naming_an_undeclared_arg_is_ignored(engine):
    """A typo in optional= must not silently add a phantom parameter."""
    spec = _spec(engine, args="filename", optional="ofset")
    params = engine.build_tools_def({"demo": spec})[0]["function"]["parameters"]
    assert params["required"] == ["filename"]
    assert set(params["properties"]) == {"filename"}


def test_schedule_task_no_longer_demands_schedule_and_task(engine):
    """action=list needs neither; requiring them forced the model to invent values."""
    assert engine.TOOL_REQUIRED_PARAMS["schedule_task"] == ["action"]
