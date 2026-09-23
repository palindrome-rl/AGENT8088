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


# Tasks with at least this much time are limited by reasoning, not turns:
# they get Qwen3 thinking on (with Qwen's thinking-mode sampling).
THINKING_BUDGET_SEC = 1500
THINKING_EXTRA_BODY = ('{"chat_template_kwargs": {"enable_thinking": true}, '
                       '"top_k": 20, "top_p": 0.95}')


def budget_edits(budget_sec: int) -> dict[str, str]:
    """Config keys that depend on one task's time budget; {} for no budget.

    The run stops itself before the watchdog kills it, no single shell call
    can outlast the run, and a hung command costs at most a third of a short
    task's time.
    """
    if budget_sec <= 90:
        return {}
    edits = {
        "max_turn_seconds": str(budget_sec - 60),
        "max_tool_timeout_seconds": str(max(60, budget_sec - 120)),
        "tool_timeout.execute_shell": "600" if budget_sec >= THINKING_BUDGET_SEC else "300",
    }
    if budget_sec >= THINKING_BUDGET_SEC:
        edits["provider.vllm.extra_body"] = THINKING_EXTRA_BODY
        edits["temperature"] = "0.6"
    return edits
