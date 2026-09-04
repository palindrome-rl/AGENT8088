"""find_tool_calls must not report a tool's arguments as *missing* when the
model actually supplied them in a shape the parser didn't anticipate.

The bug this covers: the "loose ✿FUNCTION✿ line, genuinely no args" fallback
treated "no ✿ARGS✿ marker present" as proof the model passed no arguments.
That is false in two shapes both observed live from glm-5.2 via Ollama Cloud:
the arguments arrive in a following ```json fence (which _outside_fenced_code
strips before the parser ever sees it), or on the next line without the
✿ARGS✿ marker at all. Either way the call reached the tool with {} and
browse_page answered "Error: browser tool requires 'url'" - on every retry,
because the model kept emitting the same shape and had no way to know the
url it *did* send was being discarded. A wrong-argument error that blames
the model for omitting what it supplied is worse than a parse error: it
sends the model chasing a problem that does not exist.
"""
from agent8088 import engine as A

URL = "https://example.com/app"


def _args(text, allowed={"browse_page"}):
    calls = A.find_tool_calls(text, allowed)
    assert calls, f"no tool call parsed from:\n{text}"
    assert calls[0]["name"] == "browse_page"
    return calls[0].get("arguments", {})


def test_args_in_a_following_json_fence_are_recovered():
    """The shape that produced the live failure: the model puts the argument
    object in a ```json fence under the ✿FUNCTION✿ line. _outside_fenced_code
    strips fences (so a documentation example cannot self-execute), which
    removed the arguments and left a bare function name behind."""
    text = (
        f'✿FUNCTION✿: browse_page\n'
        f'```json\n'
        f'{{"url": "{URL}", "task": "log in and create a task"}}\n'
        f'```\n'
    )

    args = _args(text)

    assert args.get("url") == URL
    assert args.get("task") == "log in and create a task"


def test_args_on_the_next_line_without_the_args_marker_are_recovered():
    text = f'✿FUNCTION✿: browse_page\n{{"url": "{URL}", "task": "read it"}}'

    args = _args(text)

    assert args.get("url") == URL


def test_args_in_an_unlabelled_fence_are_recovered():
    """Same as the json-fenced case but with a bare ``` fence."""
    text = f'✿FUNCTION✿: browse_page\n```\n{{"url": "{URL}", "task": "read it"}}\n```'

    args = _args(text)

    assert args.get("url") == URL


def test_a_tool_call_entirely_inside_a_fence_still_does_not_execute():
    """The protection the fence-stripping exists for, which recovering
    arguments must not weaken: a complete tool call shown as an EXAMPLE
    inside a code block must never run. The recovery only looks for
    arguments once a ✿FUNCTION✿ marker has already been found outside any
    fence - i.e. only when the model genuinely asked for the call."""
    text = (
        'Here is how you would call it:\n'
        '```\n'
        f'✿FUNCTION✿: browse_page ✿ARGS✿: {{"url": "{URL}", "task": "x"}}\n'
        '```\n'
    )

    assert A.find_tool_calls(text, {"browse_page"}) == []


def test_a_call_with_genuinely_no_arguments_anywhere_still_reports_none():
    """No JSON object anywhere means the model really did omit the
    arguments - that must still surface as empty args, so the tool's own
    "requires 'url'" message is the honest answer in this case."""
    text = 'I will now call ✿FUNCTION✿: browse_page'

    assert _args(text) == {}


def test_prose_after_the_function_line_is_not_mistaken_for_arguments():
    """Recovery must find an argument OBJECT, not scavenge any text - prose
    following the function name is not arguments."""
    text = '✿FUNCTION✿: browse_page\nI could not determine which URL to use.'

    assert _args(text) == {}


def test_the_canonical_args_form_is_unaffected():
    text = f'✿FUNCTION✿: browse_page ✿ARGS✿: {{"url": "{URL}", "task": "read it"}}'

    assert _args(text).get("url") == URL


def test_an_unparseable_args_block_still_reports_a_parse_error():
    """Distinct from missing arguments: the model sent an ✿ARGS✿ block that
    is broken. That must stay a parse error, not become empty args."""
    text = '✿FUNCTION✿: browse_page ✿ARGS✿: {"url": "unterminated'

    assert _args(text).get("__parse_error__")


def test_recovery_prefers_the_object_belonging_to_the_function_call():
    """A fence containing an unrelated object earlier in the reply must not
    be adopted as this call's arguments."""
    text = (
        'First, some unrelated output:\n'
        '```json\n'
        '{"status": "thinking", "confidence": 0.4}\n'
        '```\n'
        f'✿FUNCTION✿: browse_page\n'
        '```json\n'
        f'{{"url": "{URL}", "task": "read it"}}\n'
        '```\n'
    )

    args = _args(text)

    assert args.get("url") == URL
    assert "status" not in args


# --- bare {"name":..., "arguments":...} form --------------------------------


def test_bare_json_form_keeps_a_nested_argument_object():
    """The bare-JSON branch captured the arguments with a non-greedy
    `(\\{.*?\\})`, which stops at the first '}' and so truncates any nested
    object — dropping the whole call silently. This is the same flaw the
    ✿ARGS✿ branch already fixed by counting braces (_scan_json_object); this
    branch was never updated. MCP tools declare their own parameter schemas
    and can legitimately take nested objects, so this is not hypothetical."""
    text = (
        '{"name": "browse_page", "arguments": '
        f'{{"url": "{URL}", "opts": {{"deep": true, "retries": 2}}}}}}'
    )

    args = _args(text)

    assert args.get("url") == URL
    assert args.get("opts") == {"deep": True, "retries": 2}


def test_tool_call_wrapper_keeps_a_nested_argument_object():
    text = (
        '<tool_call>\n{"name": "browse_page", "arguments": '
        f'{{"url": "{URL}", "opts": {{"deep": true}}}}}}\n</tool_call>'
    )

    args = _args(text)

    assert args.get("url") == URL
    assert args.get("opts") == {"deep": True}


# --- native (provider-side) tool calls -------------------------------------


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeMessage:
    def __init__(self, *calls):
        self.tool_calls = [_FakeFunction(n, a) for n, a in calls]

    def __iter__(self):
        return iter(self.tool_calls)


class _FakeToolCall:
    def __init__(self, name, arguments):
        self.function = _FakeFunction(name, arguments)


class _NativeMessage:
    def __init__(self, *calls):
        self.tool_calls = [_FakeToolCall(n, a) for n, a in calls]


def test_a_native_call_with_valid_arguments_round_trips():
    message = _NativeMessage(("browse_page", f'{{"url": "{URL}", "task": "read it"}}'))

    args = _args(A._native_tool_text(message))

    assert args.get("url") == URL


def test_malformed_native_arguments_are_flagged_not_silently_emptied():
    """_native_tool_text used to swallow a JSONDecodeError into "{}", so a
    provider that sent a broken argument blob became a call with NO
    arguments — and browse_page answered "requires 'url'", blaming the model
    for an omission that never happened. The failure has to stay visible."""
    message = _NativeMessage(("browse_page", '{"url": "unterminated'))

    args = _args(A._native_tool_text(message))

    assert args.get("__parse_error__"), f"expected a parse error, got {args!r}"
    assert args.get("url") is None
