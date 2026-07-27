#!/usr/bin/env python3
"""
Agent8088 CLI — a Hermes-style interactive interface for fully testing Agent8088.

Imports the real Agent8088 engine (the `agent8088` script) as a module, so this
CLI drives the exact same code paths — no duplicated logic. Every Agent8088
feature is reachable here:

  • Chat            — plain text runs the full agent loop (tool-calling, reasoning,
                      multi-turn context, loop-breaking) with live tool output.
  • /tool           — invoke any single tool directly, to test each in isolation.
  • /plan           — exercise the plan-executor (multi-step decomposition).
  • /raw            — one raw model call, showing reasoning + tool_calls fields.
  • /model          — switch backend (Ornith  <->  Gemma fallback).
  • /config /system /tools /history /trace /temp /maxturns /save /clear ...

Run:  python agent8088_cli.py
"""
import sys, os, json, time, threading, select  # noqa: F401
try:
    import readline  # enables input history/editing; Unix-only
except ImportError:
    pass
from contextlib import nullcontext
from pathlib import Path

try:
    import termios, tty
except ImportError:  # not available on Windows
    termios = tty = None

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.text import Text
from rich.padding import Padding
from rich.spinner import SPINNERS, Spinner
from rich.live import Live
from rich import box

APP_DIR = Path(__file__).resolve().parent
console = Console()

# A quiet pulsing sparkle for the "thinking" indicator — same idea as Claude Code's own
# status spinner: a single soft-flashing glyph next to dim status text, not a novelty animation.
SPINNERS["agent8088_pulse"] = {
    "interval": 120,
    "frames": ["✢", "✳", "∗", "✻", "✳"],
}


class EscListener:
    """Watches stdin in raw mode for an ESC keypress without blocking the caller.

    Only does anything on a real, interactive tty; on any other stdin it's a no-op so
    piped/non-terminal runs behave exactly as before. `triggered` is a threading.Event
    that gets set the moment ESC is seen.
    """
    def __init__(self):
        self.triggered = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._old_settings = None
        self._active = termios is not None and sys.stdin.isatty()

    def __enter__(self):
        if not self._active:
            return self
        try:
            self._old_settings = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        except Exception:
            self._active = False
            return self
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def _watch(self):
        fd = sys.stdin.fileno()
        while not self._stop.is_set():
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                continue
            ch = os.read(fd, 1)
            if ch == b"\x1b":
                # Swallow any trailing bytes of an escape sequence (e.g. arrow keys)
                # so they don't leak into the next prompt.
                while select.select([fd], [], [], 0.01)[0]:
                    os.read(fd, 1)
                self.triggered.set()
                return

    def __exit__(self, *exc):
        if not self._active:
            return False
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.2)
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings)
        except Exception:
            pass
        return False


class _StatusLine:
    """Live-updating 'spinner + elapsed time + tokens' line, refreshed by Live's own
    background repaint (no manual ticking needed — elapsed/tokens are computed at render
    time, same trick Rich's own Spinner uses)."""
    def __init__(self, msg, start_time, tokens_ref, interruptible):
        self.msg = msg
        self.start_time = start_time
        self.tokens_ref = tokens_ref
        self.interruptible = interruptible
        self.spinner = Spinner("agent8088_pulse", style="cyan")

    def __rich_console__(self, console, options):
        elapsed = time.time() - self.start_time
        bits = [f"{elapsed:.0f}s"]
        if self.tokens_ref[0]:
            bits.append(f"↑{self.tokens_ref[0]} tokens")
        if self.interruptible:
            bits.append("esc to interrupt")
        grid = Table.grid(padding=(0, 1))
        grid.add_row(self.spinner, Text(f"{self.msg} ({' · '.join(bits)})", style="dim"))
        yield grid


# ---------------------------------------------------------------------------
# Load the real Agent8088 engine
# ---------------------------------------------------------------------------
from agent8088 import engine as A


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
class Session:
    def __init__(self):
        self.messages = []
        self.temperature = 0.1
        self.max_turns = 10
        self.show_trace = False
        self.show_reasoning = False
        self.last_trace = None


S = Session()


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
def banner():
    if console.is_terminal:
        print(A.make_banner())
    else:
        console.print(A.PLAINTEXT_BANNER)
    endpoint = A.APP_CONFIG.get("model_base_url", "?")
    backend = "Gemma (fallback)" if os.environ.get("USE_GEMMA4") == "1" else "Ornith / custom"
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(justify="right", style="cyan")
    tbl.add_column(style="white")
    tbl.add_row("Model", str(A.MODEL_NAME))
    tbl.add_row("Backend", backend)
    tbl.add_row("Endpoint", str(endpoint))
    tbl.add_row("Tools", f"{len(A.TOOL_NAMES)} loaded  ·  " + ", ".join(sorted(A.TOOL_NAMES)))
    tbl.add_row("Temp", str(S.temperature))
    tbl.add_row("Max turns", str(S.max_turns))
    console.print(Panel(tbl, title="[bold]Agent8088 CLI[/bold]",
                        subtitle="type /help for commands", box=box.ROUNDED, border_style="cyan"))


def status_cm(msg):
    """spin() hook for run_agent — a rich status spinner as a context manager."""
    return console.status(f"[dim]{msg}[/dim]", spinner="agent8088_pulse", spinner_style="cyan")


# run_agent presentation hooks -> rich output
#
# NOTE: tool names/args/results all originate from the model or from files on disk, so
# none of it is trusted to be free of "[" — everything user-controlled is composed with
# Text() (literal, no markup parsing) rather than interpolated into console.print(f"...").
def _format_args(args):
    return ", ".join(f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}" for k, v in (args or {}).items())


def on_calls(calls):
    for call in calls:
        line = Text()
        line.append("⏺ ", style="cyan")
        line.append(call["name"], style="bold")
        line.append("(" + _format_args(call.get("arguments")) + ")")
        console.print(line)


def on_tool(name):
    pass  # covered by on_calls; the spinner shows "running <name>..."


def _numbered_lines(text, limit=40):
    lines = text.splitlines()
    total = len(lines)
    width = len(str(min(total, limit)))
    body = Text()
    for i, line_text in enumerate(lines[:limit], 1):
        body.append(f"{i:>{width}}  ", style="dim")
        body.append(line_text + "\n")
    if total > limit:
        body.append(f"… {total - limit} more line{'s' if total - limit != 1 else ''}", style="dim italic")
    return body, total


def _diff_block(diff_lines, limit=60):
    body = Text()
    shown = diff_lines[:limit]
    for line in shown:
        if line.startswith(("+++", "---")):
            body.append(line + "\n", style="dim")
        elif line.startswith("+"):
            body.append(line + "\n", style="green")
        elif line.startswith("-"):
            body.append(line + "\n", style="red")
        elif line.startswith("@@"):
            body.append(line + "\n", style="cyan")
        else:
            body.append(line + "\n", style="dim")
    if len(diff_lines) > limit:
        body.append(f"… {len(diff_lines) - limit} more diff lines", style="dim italic")
    return body


def on_result(name, result):
    mode = A.TOOL_SPECS.get(name, {}).get("mode")

    if mode == "read_text":
        body, total = _numbered_lines(result)
        console.print(Text(f"  ⎿  Read {total} line{'s' if total != 1 else ''}", style="dim"))
        console.print(Padding(body, (0, 0, 0, 5)))
        return

    if mode == "write_text" and A._last_write_diff:
        console.print(Text(f"  ⎿  {result}", style="dim"))
        console.print(Padding(_diff_block(A._last_write_diff), (0, 0, 0, 5)))
        return

    preview = result.strip().replace("\n", " ")
    if len(preview) > 180:
        preview = preview[:180] + "…"
    lines = result.count("\n") + 1
    line = Text("  ⎿  ", style="dim")
    line.append(preview)
    if lines > 1:
        line.append(f"  ({lines} lines)", style="dim")
    console.print(line)


def _handle_escalation(result_text, messages=None, live=None):
    """Check if a tool result is an escalation request. If so, prompt the user
    with an Allow/Decline selection and call grant_escalation() if approved.
    Returns True if the result was an escalation request (handled or denied),
    False otherwise. If messages is provided, injects a retry hint on approval.
    If live is provided, stops it before prompting and resumes after."""
    if not result_text.startswith("ESCALATION_REQUEST:"):
        return False
    parts = result_text.split(":", 4)
    if len(parts) < 5:
        return False
    _, target_mode, change_type, paths, reason = parts

    # Stop the live display so the prompt can capture stdin
    if live is not None:
        live.stop()

    console.print()
    console.print(Panel(
        Text(f"{reason}\n\nPaths: {paths}\nChange type: {change_type}\nRequested mode: {target_mode}"),
        title="[bold yellow]Permission Escalation Request[/bold yellow]",
        box=box.ROUNDED, border_style="yellow",
    ))
    try:
        response = console.input("[bold yellow]Allow? (y/n): [/bold yellow]").strip().lower()
    except (EOFError, KeyboardInterrupt):
        response = "n"

    if response in ("y", "yes"):
        A.grant_escalation()
        console.print("[green]Approved for this action only. Next write will ask again.[/green]")
        if messages is not None:
            messages.append({"role": "user", "content":
                "Permission granted for this action only. Retry the tool call that was blocked. "
                "Note: each new write or system command will require separate approval."})
    else:
        console.print("[red]Permission denied — staying in readonly mode.[/red]")
        if messages is not None:
            messages.append({"role": "user", "content":
                "Permission denied by the user. You remain in readonly mode. "
                "Tell the user what you could not do and why the task cannot be completed."})

    # Resume the live display
    if live is not None:
        live.start()

    return True


def render_answer(answer):
    if not answer:
        console.print("[dim](no answer)[/dim]")
        return
    try:
        console.print(Panel(Markdown(answer), title="[bold cyan]Agent8088[/bold cyan]",
                            box=box.ROUNDED, border_style="cyan"))
    except Exception:
        console.print(Panel(Text(answer), title="[bold cyan]Agent8088[/bold cyan]",
                            box=box.ROUNDED, border_style="cyan"))


# ---------------------------------------------------------------------------
# Chat turn (drives the real run_agent)
# ---------------------------------------------------------------------------
def _stream_view(reasoning_parts, content_parts):
    """While generating: reasoning (if any) shown dim/italic above the growing answer,
    so the model's chain-of-thought never gets mistaken for its actual reply."""
    blocks = []
    if reasoning_parts:
        blocks.append(Panel(Text("".join(reasoning_parts), style="dim italic"),
                            title="[dim]thinking[/dim]", box=box.MINIMAL, border_style="grey50"))
    if content_parts:
        blocks.append(Panel(Text("".join(content_parts)), title="[bold cyan]Agent8088[/bold cyan]",
                            box=box.ROUNDED, border_style="cyan"))
    return Group(*blocks) if blocks else Text("")


def do_chat(query):
    S.messages.append({"role": "user", "content": query})
    trace = [] if S.show_trace else None
    reasoning_parts, content_parts = [], []
    tokens_ref = [0]
    turn_start = time.time()
    esc = EscListener()

    with esc, Live(console=console, refresh_per_second=20, transient=True) as live:
        def spin(msg):
            live.update(_StatusLine(msg, turn_start, tokens_ref, interruptible=msg.startswith("thinking")))
            return nullcontext()

        def on_token(kind, delta):
            tokens_ref[0] += 1
            (reasoning_parts if kind == "reasoning" else content_parts).append(delta)
            live.update(_stream_view(reasoning_parts, content_parts))

        def _on_result(name, result):
            on_result(name, result)
            if _handle_escalation(result, S.messages, live):
                # Escalation was handled. If granted, a retry hint was injected
                # into S.messages so the model will re-attempt the blocked tool.
                pass

        try:
            answer = A.run_agent(
                S.messages, max_turns=S.max_turns, temperature=S.temperature,
                spin=spin, on_calls=on_calls, on_tool=on_tool,
                on_result=_on_result, on_answer=None, on_token=on_token,
                interrupt_check=esc.triggered.is_set, trace=trace,
            )
        except A.AgentInterrupted:
            answer = None

    elapsed = time.time() - turn_start
    if answer is None:
        partial = "".join(content_parts).strip()
        if partial:
            render_answer(partial)
        console.print(f"[dim]⏹ interrupted · {elapsed:.1f}s[/dim]")
        return

    render_answer(answer)
    console.print(f"[dim]{elapsed:.1f}s · ↑{tokens_ref[0]} tokens[/dim]")
    if trace is not None:
        S.last_trace = trace
        console.print(Panel(Text(json.dumps(trace, indent=2)), title="[magenta]trace[/magenta]",
                            box=box.MINIMAL, border_style="magenta"))


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
def parse_tool_args(raw):
    """Accept either JSON ({"k":"v"}) or key=value pairs."""
    raw = raw.strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        return json.loads(raw)
    args = {}
    for pair in raw.split():
        if "=" in pair:
            k, v = pair.split("=", 1)
            args[k.strip()] = v.strip()
    return args


def cmd_help(_):
    t = Table(title="Commands", box=box.SIMPLE, title_style="bold cyan")
    t.add_column("Command", style="yellow", no_wrap=True)
    t.add_column("What it does")
    rows = [
        ("<text>", "Chat — run the full agent loop on your message"),
        ("/tools", "List every tool with its args, mode, and description"),
        ("/tool <name> <args>", "Invoke ONE tool directly (args as JSON or key=value)"),
        ("/plan <steps>", "Test the plan-executor (newline- or JSON-separated steps)"),
        ("/raw <text>", "One raw model call — shows content, reasoning, tool_calls"),
        ("/model [ornith|gemma]", "Show or switch the backend model"),
        ("/config", "Show the active configuration (model, endpoint, paths)"),
        ("/system", "Show the full system prompt sent to the model"),
        ("/history", "Show the current conversation"),
        ("/trace [on|off]", "Toggle capturing/printing the step-by-step JSON trace"),
        ("/temp <float>", "Set sampling temperature (current: %s)" % S.temperature),
        ("/maxturns <int>", "Set max agent turns (current: %s)" % S.max_turns),
        ("/save <file>", "Save conversation + last trace to a JSON file"),
        ("/clear", "Clear the conversation context"),
        ("/help", "Show this list"),
        ("/exit, /quit", "Leave"),
    ]
    for a, b in rows:
        t.add_row(a, b)
    console.print(t)


def cmd_tools(_):
    t = Table(title="Tools", box=box.SIMPLE_HEAVY, title_style="bold cyan")
    t.add_column("Name", style="yellow")
    t.add_column("Args", style="green")
    t.add_column("Mode", style="magenta")
    t.add_column("Description")
    for name in sorted(A.TOOL_SPECS):
        spec = A.TOOL_SPECS[name]
        args = ", ".join(spec.get("args") or []) or "—"
        t.add_row(name, args, spec.get("mode", "?"), spec.get("description", ""))
    console.print(t)


def cmd_tool(rest):
    parts = rest.split(None, 1)
    if not parts:
        console.print("[red]usage:[/red] /tool <name> <json-or-key=value args>")
        return
    name = parts[0]
    if name not in A.TOOL_NAMES:
        console.print(f"[red]unknown tool:[/red] {name}  (see /tools)")
        return
    try:
        args = parse_tool_args(parts[1] if len(parts) > 1 else "")
    except Exception as e:
        console.print(f"[red]could not parse args:[/red] {e}")
        return
    with status_cm(f"running {name}..."):
        result = A.exec_tool(name, json.dumps(args))
    console.print(Panel(Text(result), title=f"[green]{name}[/green]  {json.dumps(args)}",
                        box=box.ROUNDED, border_style="green"))


_PLAN_ICONS = {"pending": ("○", "dim"), "running": ("◐", "yellow"), "done": ("✓", "green")}


def cmd_plan(rest):
    if not rest.strip():
        console.print("[red]usage:[/red] /plan <step1\\n step2 ...>  or  /plan [\"step1\",\"step2\"]")
        return

    steps_state = {}

    def render_checklist():
        rows = []
        for idx in sorted(steps_state):
            step_text, tool_name, status = steps_state[idx]
            icon, style = _PLAN_ICONS[status]
            row = Text()
            row.append(f"{icon} ", style=style)
            row.append(f"[{idx}] ", style="dim")
            row.append(f"{tool_name}: ", style="bold")
            row.append(step_text[:70])
            rows.append(row)
        return Group(*rows) if rows else Text("planning...")

    def on_step(idx, total, step_text, tool_name, status, result):
        steps_state[idx] = (step_text, tool_name, status)
        live.update(render_checklist())

    with Live(console=console, refresh_per_second=10, transient=False) as live:
        result = A._exec_plan({"steps": rest}, on_step=on_step)

    console.print(Panel(Text(result), title="[green]plan result[/green]",
                        box=box.ROUNDED, border_style="green"))


def cmd_raw(rest):
    if not rest.strip():
        console.print("[red]usage:[/red] /raw <prompt>")
        return
    msgs = [{"role": "user", "content": rest}]
    with status_cm("raw completion..."):
        resp = A.create_completion(A.client, msgs, A.TOOLS_DEF, temperature=S.temperature)
    m = resp.choices[0].message
    content = m.content or ""
    reasoning = getattr(m, "reasoning_content", "") or ""
    tcs = getattr(m, "tool_calls", None) or []
    console.print(Panel(Text(content or "(empty)"), title="content", box=box.MINIMAL, border_style="cyan"))
    if reasoning:
        console.print(Panel(Text(reasoning), title="reasoning_content", box=box.MINIMAL, border_style="blue"))
    if tcs:
        rows = "\n".join(f"{tc.function.name}({tc.function.arguments})" for tc in tcs)
        console.print(Panel(Text(rows), title="tool_calls", box=box.MINIMAL, border_style="yellow"))
    fr = resp.choices[0].finish_reason
    console.print(f"[dim]finish_reason={fr}[/dim]")


def cmd_model(rest):
    arg = rest.strip().lower()
    if not arg:
        backend = "Gemma (fallback)" if os.environ.get("USE_GEMMA4") == "1" else "Ornith / custom"
        console.print(f"Current: [cyan]{A.MODEL_NAME}[/cyan]  ({backend})")
        console.print("Switch with: [yellow]/model ornith[/yellow] or [yellow]/model gemma[/yellow]")
        return
    if arg in ("gemma", "gemma4"):
        os.environ["USE_GEMMA4"] = "1"
    elif arg in ("ornith", "custom", "default"):
        os.environ.pop("USE_GEMMA4", None)
    else:
        console.print("[red]unknown model[/red] — use 'ornith' or 'gemma'")
        return
    A.client, A.MODEL_NAME = A.get_client()
    console.print(f"[green]switched[/green] → [cyan]{A.MODEL_NAME}[/cyan]")


def cmd_config(_):
    t = Table(title="Configuration", box=box.SIMPLE, title_style="bold cyan")
    t.add_column("Key", style="cyan")
    t.add_column("Value")
    keys = ["model_base_url", "model_name", "timeout_seconds", "project_root",
            "shell_cwd", "allowed_paths", "search_base_url", "gemma_base_url", "gemma_model_name"]
    for k in keys:
        v = A.APP_CONFIG.get(k, "—")
        if k == "api_key":
            v = "[hidden]"
        t.add_row(k, str(v))
    t.add_row("[dim]resolved model[/dim]", str(A.MODEL_NAME))
    console.print(t)
    console.print(f"[dim]config file: {os.environ.get('AGENT8088_CONFIG', str(APP_DIR / 'config.txt'))}[/dim]")


def cmd_system(_):
    console.print(Panel(Text(A.SYSTEM_PROMPT), title="System Prompt", box=box.ROUNDED, border_style="blue"))


def cmd_history(_):
    if not S.messages:
        console.print("[dim](conversation empty)[/dim]")
        return
    for msg in S.messages:
        role = msg["role"]
        style = {"user": "green", "assistant": "cyan", "system": "blue"}.get(role, "white")
        console.print(f"[{style} bold]{role}:[/{style} bold] {msg['content'][:1000]}")


def cmd_trace(rest):
    arg = rest.strip().lower()
    if arg == "on":
        S.show_trace = True
    elif arg == "off":
        S.show_trace = False
    else:
        S.show_trace = not S.show_trace
    console.print(f"trace capture: [{'green' if S.show_trace else 'red'}]{'on' if S.show_trace else 'off'}[/]")


def cmd_temp(rest):
    try:
        S.temperature = float(rest.strip())
        console.print(f"temperature = [cyan]{S.temperature}[/cyan]")
    except Exception:
        console.print("[red]usage:[/red] /temp <float>")


def cmd_maxturns(rest):
    try:
        S.max_turns = int(rest.strip())
        console.print(f"max_turns = [cyan]{S.max_turns}[/cyan]")
    except Exception:
        console.print("[red]usage:[/red] /maxturns <int>")


def cmd_save(rest):
    path = rest.strip() or "agent8088_session.json"
    data = {"model": A.MODEL_NAME, "messages": S.messages, "trace": S.last_trace}
    Path(path).write_text(json.dumps(data, indent=2))
    console.print(f"[green]saved[/green] → {path}")


def cmd_clear(_):
    S.messages.clear()
    S.last_trace = None
    console.print("[green]context cleared[/green]")


COMMANDS = {
    "help": cmd_help, "tools": cmd_tools, "tool": cmd_tool, "plan": cmd_plan,
    "raw": cmd_raw, "model": cmd_model, "config": cmd_config, "system": cmd_system,
    "history": cmd_history, "trace": cmd_trace, "temp": cmd_temp,
    "maxturns": cmd_maxturns, "save": cmd_save, "clear": cmd_clear,
}


# ---------------------------------------------------------------------------
# Main REPL
# ---------------------------------------------------------------------------
def _estimate_context_pct():
    """Rough ~4-chars-per-token estimate against CONTEXT_WINDOW — good enough for a
    progress hint, not meant to be exact."""
    chars = len(A.SYSTEM_PROMPT) + sum(len(m.get("content") or "") for m in S.messages)
    if not A.CONTEXT_WINDOW:
        return 0
    return min(100, int(100 * (chars // 4) / A.CONTEXT_WINDOW))


def _prompt_label():
    pct = _estimate_context_pct()
    return f"\n[bold green]8088[/bold green] [dim]({pct}% ctx)[/dim] [dim]›[/dim] "


def _agent8088_home():
    """Find the agent8088 install home directory."""
    return Path(os.environ.get("AGENT8088_HOME", os.path.join(
        os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")), "agent8088")))


def _run_update():
    """Pull latest code + reinstall the package in the venv."""
    import subprocess
    home = _agent8088_home()
    install_dir = home / "agent8088"
    if not install_dir.exists():
        print(f"Install dir not found: {install_dir}")
        print("Run the installer first:  iex (irm https://<YOUR-URL>/install.ps1)")
        return
    venv_subdir = "Scripts" if os.name == "nt" else "bin"
    venv_python = install_dir / "venv" / venv_subdir / ("python.exe" if os.name == "nt" else "python")
    uv_cmd = home / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    if not uv_cmd.exists():
        uv_cmd = "uv"
    print(f"Updating {install_dir} ...")
    # Stash local changes (editable install / wizard edits) before pulling
    subprocess.run(["git", "stash", "push", "--include-untracked", "-m", "agent8088-update-autostash"],
                   cwd=str(install_dir), capture_output=True, text=True)
    r = subprocess.run(["git", "pull", "--ff-only"], cwd=str(install_dir), capture_output=True, text=True)
    if r.returncode != 0:
        # FF not possible — reset to remote (managed install, local changes were stashed)
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                cwd=str(install_dir), capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "fetch", "origin"], cwd=str(install_dir), capture_output=True, text=True)
        r2 = subprocess.run(["git", "reset", "--hard", f"origin/{branch}"],
                             cwd=str(install_dir), capture_output=True, text=True)
        if r2.returncode != 0:
            print(f"git update failed:\n{r.stderr.strip()}\n{r2.stderr.strip()}")
            return
        print(f"Reset to origin/{branch}")
    else:
        print(r.stdout.strip() or "Already up to date.")
    r3 = subprocess.run([str(uv_cmd), "pip", "install", "--python", str(venv_python), "-e", str(install_dir)],
                        capture_output=True, text=True)
    if r3.returncode != 0:
        print(f"reinstall failed:\n{r3.stderr.strip()}")
        return
    print("Updated. Run 'agent8088 --version' to verify.")


def _run_setup():
    """Interactive config wizard — prompt for model endpoint, write to config.txt."""
    import re as _re
    home = _agent8088_home()
    config_path = Path(os.environ.get("AGENT8088_CONFIG", str(home / "config.txt")))
    if not config_path.exists():
        print(f"Config not found: {config_path}")
        print("Run the installer first.")
        return
    content = config_path.read_text(encoding="utf-8")
    def _current(key):
        m = _re.search(rf'^{key}=(.*)$', content, _re.MULTILINE)
        return m.group(1).strip() if m else ""
    print("Agent8088 setup")
    print("  (Press Enter to keep the current value in brackets)\n")
    cur_paths = _current("allowed_paths") or "~"
    paths = input(f"Working directory [{cur_paths}]: ").strip() or cur_paths
    cur_url = _current("model_base_url") or "http://localhost:11434/v1"
    url = input(f"Model base URL [{cur_url}]: ").strip() or cur_url
    cur_name = _current("model_name") or "qwen14b-tooluse-v3"
    name = input(f"Model name [{cur_name}]: ").strip() or cur_name
    cur_key = _current("api_key") or "ollama"
    key = input(f"API key [{cur_key}]: ").strip() or cur_key
    cur_search = _current("search_base_url")
    search = input(f"Web search URL [{cur_search or 'disabled'}]: ").strip()
    content = _re.sub(r'^allowed_paths=.*', f'allowed_paths={paths}', content, flags=_re.MULTILINE)
    content = _re.sub(r'^model_base_url=.*', f'model_base_url={url}', content, flags=_re.MULTILINE)
    content = _re.sub(r'^model_name=.*', f'model_name={name}', content, flags=_re.MULTILINE)
    content = _re.sub(r'^api_key=.*', f'api_key={key}', content, flags=_re.MULTILINE)
    if search:
        if _re.search(r'^#?\s*search_base_url=', content, _re.MULTILINE):
            content = _re.sub(r'^#?\s*search_base_url=.*', f'search_base_url={search}', content, flags=_re.MULTILINE)
        else:
            content += f"\nsearch_base_url={search}\n"
    config_path.write_text(content, encoding="utf-8")
    print(f"\nConfig written to {config_path}")


def main():
    import argparse
    from agent8088 import __version__
    parser = argparse.ArgumentParser(
        prog="agent8088",
        description="Agent8088 - Local AI Assistant",
        epilog="Run with no flags to start the interactive REPL.",
    )
    parser.add_argument("--version", "-V", action="version", version=f"agent8088 {__version__}")
    parser.add_argument("--edit", action="store_true", help="start in edit mode (no per-action permission prompts)")
    parser.add_argument("--uninstall", action="store_true", help="remove agent8088 install dir + env vars, then exit")
    parser.add_argument("--update", action="store_true", help="pull latest code + reinstall, then exit")
    parser.add_argument("--setup", action="store_true", help="run interactive config wizard, then exit")
    args = parser.parse_args()

    if args.uninstall:
        import shutil
        home = _agent8088_home()
        print(f"Removing {home} ...")
        if home.exists():
            shutil.rmtree(home, ignore_errors=True)
        os.environ.pop("AGENT8088_CONFIG", None)
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(k, "AGENT8088_CONFIG")
            winreg.CloseKey(k)
        except Exception:
            pass
        print("Done. Open a NEW terminal for PATH to refresh.")
        return
    if args.update:
        _run_update()
        return
    if args.setup:
        _run_setup()
        return
    if args.edit:
        A.PERMISSION_MODE = "edit"
    banner()
    while True:
        try:
            line = console.input(_prompt_label()).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            break
        if not line:
            continue
        if line in ("/exit", "/quit", "exit", "quit"):
            console.print("[dim]bye[/dim]")
            break
        if line.startswith("/"):
            cmd, _, rest = line[1:].partition(" ")
            handler = COMMANDS.get(cmd.lower())
            if handler:
                try:
                    handler(rest)
                except Exception as e:
                    console.print(f"[red]error:[/red] {e}")
            else:
                console.print(f"[red]unknown command:[/red] /{cmd}  (try /help)")
            continue
        try:
            do_chat(line)
        except KeyboardInterrupt:
            console.print("\n[dim]interrupted[/dim]")
        except Exception as e:
            console.print(f"[red]error:[/red] {e}")


if __name__ == "__main__":
    main()