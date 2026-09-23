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
