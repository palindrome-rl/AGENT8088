"""Shell command that rewrites key=value lines of an agent8088 config.txt.

Kept free of Harbor imports so it can be tested without Harbor installed.
"""
import shlex


def config_edit_command(config_path: str, edits: dict[str, str]) -> str:
    """Drop every existing line for each key in `edits`, then append key=value.

    Uses only grep/printf/mv: the task container may have no Python.
    """
    if not edits:
        return "true"
    pattern = "^(" + "|".join(key.replace(".", "\\.") for key in edits) + ")="
    new = f"{config_path}.new"
    steps = [f"{{ grep -v -E {shlex.quote(pattern)} {shlex.quote(config_path)} || true; }} > {shlex.quote(new)}"]
    steps += [f"printf '%s\\n' {shlex.quote(f'{k}={v}')} >> {shlex.quote(new)}" for k, v in edits.items()]
    steps.append(f"mv {shlex.quote(new)} {shlex.quote(config_path)}")
    return " && ".join(steps)


# Tasks with at least this much time get the longer default shell timeout.
LONG_BUDGET_SEC = 1500
# The loop's own stop comes this long before the watchdog. Kept small so the
# agent gets nearly the task's full time, like the other agents compared.
SELF_STOP_SEC = 15


def budget_edits(budget_sec: int) -> dict[str, str]:
    """Config keys that depend on one task's time budget; {} for no budget.

    The run tries to stop itself shortly before the watchdog (SELF_STOP_SEC);
    if a long model call overruns that, the watchdog's SIGTERM saves the trace.
    No single shell call can outlast the run, and a hung command costs at most
    a third of a short task's time.
    """
    if budget_sec <= 90:
        return {}
    return {
        "max_turn_seconds": str(budget_sec - SELF_STOP_SEC),
        "max_tool_timeout_seconds": str(max(60, budget_sec - 120)),
        "tool_timeout.execute_shell": "600" if budget_sec >= LONG_BUDGET_SEC else "300",
    }


def trace_token_totals(data) -> tuple[int, int] | None:
    """(input, output) tokens summed over an agent8088 trace export's turns.

    Each turn carries "usage" ({"input_tokens", "output_tokens"}, from the
    engine's turn budget). None when no turn has usage (older agent8088).
    """
    turns = data.get("trace") if isinstance(data, dict) else None
    found, tokens_in, tokens_out = False, 0, 0
    for turn in turns or []:
        usage = turn.get("usage") if isinstance(turn, dict) else None
        if isinstance(usage, dict):
            found = True
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
    return (tokens_in, tokens_out) if found else None


# The execute_shell description in tools.txt states a default and a maximum
# timeout. The real values depend on the task (budget_edits): on a 900 s task
# the default is 300 s and the cap 760 s, not the 600/1800 the text claimed, so
# a model relying on the stated default had long commands killed unexpectedly.
SHELL_TIMEOUT_SENTENCE = ("Pass timeout (seconds, up to 1800) for installs, builds and long runs; "
                          "the default is 600.")


def shell_timeout_sentence(default_sec: str, max_sec: str) -> str:
    return (f"Pass timeout (seconds, up to {max_sec}) for installs, builds and long runs; "
            f"the default is {default_sec}.")


def _sed_escape(text: str, *, replacement: bool = False) -> str:
    """Escape for a sed s#...#...# expression (basic regex / replacement)."""
    specials = "\\#&" if replacement else "\\#.[]*^$"
    return "".join("\\" + ch if ch in specials else ch for ch in text)


def tools_description_command(tools_path: str, budget_sec: int) -> str:
    """Shell command writing this task's real shell timeouts into tools.txt.

    grep/sed only (the task container may have no Python). A missing sentence
    is reported, never fatal: the run must not fail on a description edit.
    """
    edits = budget_edits(budget_sec)
    if not edits:
        return "true"
    new = shell_timeout_sentence(edits["tool_timeout.execute_shell"], edits["max_tool_timeout_seconds"])
    expr = f"s#{_sed_escape(SHELL_TIMEOUT_SENTENCE)}#{_sed_escape(new, replacement=True)}#"
    path = shlex.quote(tools_path)
    return (f"if grep -qF {shlex.quote(SHELL_TIMEOUT_SENTENCE)} {path}; then "
            f"sed -i {shlex.quote(expr)} {path}; "
            f"else echo 'agent8088: execute_shell timeout sentence not found in tools.txt' >&2; fi")
