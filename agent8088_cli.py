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
from importlib.machinery import SourceFileLoader
import importlib.util

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


class _SubStatusLine:
    """Animated status line for a running sub-agent: a magenta gutter, a pulsing
    spinner, and the sub-agent's current activity + elapsed time. Like _StatusLine,
    it recomputes at render time so Live's background repaint animates it for free
    even while the model call blocks."""
    def __init__(self, state):
        self.state = state
        self.spinner = Spinner("agent8088_pulse", style="magenta")

    def __rich_console__(self, console, options):
        elapsed = time.time() - self.state["start"]
        grid = Table.grid(padding=(0, 1))
        label = Text(f"{self.state['type']} · {self.state['msg']} ({elapsed:.0f}s)", style="dim")
        grid.add_row(Text("│", style="magenta"), self.spinner, label)
        yield grid


# ---------------------------------------------------------------------------
# Load the real Agent8088 engine (script has no .py extension)
# ---------------------------------------------------------------------------
def load_engine():
    loader = SourceFileLoader("agent8088_core", str(APP_DIR / "agent8088"))
    spec = importlib.util.spec_from_loader("agent8088_core", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


A = load_engine()


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
    tbl.add_row("Subagents", f"{len(A.SUBAGENT_SPECS)} loaded  ·  " + ", ".join(sorted(A.SUBAGENT_SPECS))
                + "  [dim](/agent to run)[/dim]")
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

    if mode == "subagent":
        console.print(Panel(Text(result), title="[magenta]subagent result[/magenta]",
                            box=box.ROUNDED, border_style="magenta"))
        return

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
# Sub-agent live view — a nested, animated activity trace inside the parent turn
# ---------------------------------------------------------------------------
def _make_subagent_ui(live):
    """Factory the engine calls (via A.subagent_ui) each time a sub-agent spawns.

    Reuses the parent turn's Live: the sub-agent's status animates in the live
    region (magenta pulse), while its tool calls/results print into the scrollback
    as an indented, magenta-gutter trace — so delegation reads as a nested block:

        ⏺ spawn_subagent(agent_type="explore", task="…")
        ╭─ 🤖 subagent · explore
        │  find every TODO in the repo
        │  ⏺ execute_shell(command="grep -rn TODO")
        │  ⎿  src/app.py:12: # TODO: handle retries  (3 lines)
        ╰─ ✓ done · 1 tool · 2.4s
    """
    def factory(agent_type, task, depth):
        state = {"type": agent_type, "start": time.time(), "msg": "starting…", "tools": 0}

        head = Text("╭─ ", style="magenta")
        head.append("🤖 subagent", style="bold magenta")
        head.append(f" · {agent_type}", style="magenta")
        console.print(head)
        task_line = Text("│  ", style="magenta")
        task_line.append((task or "").strip()[:100], style="dim italic")
        console.print(task_line)

        def spin(msg):
            state["msg"] = msg
            live.update(_SubStatusLine(state))
            return nullcontext()

        def sub_on_calls(calls):
            for call in calls:
                line = Text("│  ", style="magenta")
                line.append("⏺ ", style="cyan")
                line.append(call["name"], style="bold")
                line.append("(" + _format_args(call.get("arguments")) + ")")
                console.print(line)

        def sub_on_result(name, result):
            state["tools"] += 1
            preview = result.strip().replace("\n", " ")
            if len(preview) > 120:
                preview = preview[:120] + "…"
            line = Text("│  ", style="magenta")
            line.append("⎿  ", style="dim")
            line.append(preview, style="dim")
            console.print(line)

        def done(answer):
            elapsed = time.time() - state["start"]
            n = state["tools"]
            foot = Text("╰─ ", style="magenta")
            foot.append("✓ ", style="green")
            foot.append(f"done · {n} tool{'s' if n != 1 else ''} · {elapsed:.1f}s", style="dim")
            console.print(foot)

        return {"spin": spin, "on_calls": sub_on_calls, "on_result": sub_on_result, "done": done}

    return factory


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

        # Let sub-agents render their own nested, animated activity in this Live.
        A.subagent_ui = _make_subagent_ui(live)
        try:
            answer = A.run_agent(
                S.messages, max_turns=S.max_turns, temperature=S.temperature,
                spin=spin, on_calls=on_calls, on_tool=on_tool,
                on_result=on_result, on_answer=None, on_token=on_token,
                interrupt_check=esc.triggered.is_set, trace=trace,
            )
        except A.AgentInterrupted:
            answer = None
        finally:
            A.subagent_ui = None

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
        ("/agents", "List available sub-agent profiles"),
        ("/agent [name] [task]", "Run a sub-agent — no args opens an arrow-key picker"),
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


def _read_key(fd):
    """Read one keypress in cbreak mode, decoding arrow keys.
    Returns 'up'/'down'/'left'/'right'/'enter'/'esc' or the literal character."""
    b = os.read(fd, 1)
    if b == b"\x1b":  # ESC — maybe the start of an arrow-key sequence
        seq = b""
        while select.select([fd], [], [], 0.02)[0]:
            seq += os.read(fd, 1)
        if seq[:1] == b"[":
            return {b"A": "up", b"B": "down", b"C": "right", b"D": "left"}.get(seq[1:2], "esc")
        return "esc"
    if b in (b"\r", b"\n"):
        return "enter"
    try:
        return b.decode()
    except Exception:
        return "?"


def _agent_menu(profiles, names, idx):
    """Render the arrow-key picker: the highlighted row gets a ▶ marker and reverse-video
    name chip; descriptions wrap cleanly aligned in their own column."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=1)                          # ▶ marker
    grid.add_column(no_wrap=True, min_width=16)       # profile name
    grid.add_column(overflow="fold", ratio=1)         # description (wraps aligned)
    for i, n in enumerate(names):
        selected = i == idx
        marker = Text("▶", style="bold magenta") if selected else Text(" ")
        name = Text(f" {n} ", style="bold black on magenta") if selected else Text(n, style="yellow")
        desc = Text(profiles[n].get("description", ""), style="white" if selected else "dim")
        grid.add_row(marker, name, desc)
    hint = Text("↑/↓ move · ⏎ run · esc cancel", style="dim")
    return Panel(Group(grid, Text(""), hint), title="[bold magenta]🤖 pick a sub-agent[/bold magenta]",
                box=box.ROUNDED, border_style="magenta", padding=(1, 2))


def select_agent(profiles):
    """Interactive arrow-key picker over sub-agent profiles.
    Returns the chosen name, or None on cancel / non-interactive stdin."""
    names = sorted(profiles)
    if not names or termios is None or not sys.stdin.isatty():
        return None
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    idx = 0
    try:
        tty.setcbreak(fd)
        with Live(console=console, refresh_per_second=30, transient=True) as live:
            while True:
                live.update(_agent_menu(profiles, names, idx))
                key = _read_key(fd)
                if key == "up":
                    idx = (idx - 1) % len(names)
                elif key == "down":
                    idx = (idx + 1) % len(names)
                elif key == "enter":
                    return names[idx]
                elif key in ("esc", "q"):
                    return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _run_subagent(name, task):
    """Run one sub-agent directly (no parent model) with the animated nested view."""
    with Live(console=console, refresh_per_second=20, transient=True) as live:
        A.subagent_ui = _make_subagent_ui(live)
        try:
            result = A._exec_subagent({"agent_type": name, "task": task}, depth=0)
        finally:
            A.subagent_ui = None
    # Strip the "[subagent:name] " prefix before rendering the summary panel.
    answer = result.split("] ", 1)[1] if result.startswith("[subagent:") else result
    render_answer(answer)


def cmd_agent(rest):
    """Run a sub-agent. Usage: /agent  (interactive picker) | /agent <name> [task]."""
    rest = (rest or "").strip()
    name, task = None, None
    if rest:
        first, _, remainder = rest.partition(" ")
        if first in A.SUBAGENT_SPECS:
            name, task = first, remainder.strip()
        else:
            task = rest  # not a known profile -> treat the whole line as the task
    if not name:
        name = select_agent(A.SUBAGENT_SPECS)
        if not name:
            console.print("[dim]cancelled — try /agent <name> <task>, or /agents to list them[/dim]")
            return
    if not task:
        try:
            task = console.input(f"[magenta]task for [bold]{name}[/bold] ›[/magenta] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]cancelled[/dim]")
            return
        if not task:
            console.print("[dim]cancelled — no task given[/dim]")
            return
    _run_subagent(name, task)


def cmd_agents(_):
    t = Table(title="Subagents", box=box.SIMPLE_HEAVY, title_style="bold magenta",
              caption="run one with  /agent  (arrow-key picker)  or  /agent <name> <task>",
              caption_style="dim")
    t.add_column("Name", style="yellow")
    t.add_column("Max turns", style="green")
    t.add_column("Tools", style="cyan")
    t.add_column("Description")
    for name in sorted(A.SUBAGENT_SPECS):
        p = A.SUBAGENT_SPECS[name]
        tools = ", ".join(t_ for t_ in p["tools"] if t_ in A.TOOL_NAMES) or "—"
        t.add_row(name, str(p["max_turns"]), tools, p["description"])
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
    "help": cmd_help, "tools": cmd_tools, "tool": cmd_tool,
    "agents": cmd_agents, "agent": cmd_agent, "plan": cmd_plan,
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


def _completer(text, state):
    """Tab-completion: '/<cmd>', profile names after '/agent ', tool names after '/tool '."""
    if "readline" not in sys.modules:
        return None
    buf = readline.get_line_buffer().lstrip()
    if buf.startswith("/agent "):
        matches = [n for n in sorted(A.SUBAGENT_SPECS) if n.startswith(text)]
    elif buf.startswith("/tool "):
        matches = [n for n in sorted(A.TOOL_NAMES) if n.startswith(text)]
    elif buf.startswith("/"):
        matches = ["/" + c for c in sorted(COMMANDS) if ("/" + c).startswith(text)]
    else:
        matches = []
    return matches[state] if state < len(matches) else None


def _install_completion():
    if "readline" not in sys.modules:
        return
    try:
        readline.set_completer_delims(" \t\n")  # keep '/' and names as one token
        readline.set_completer(_completer)
        readline.parse_and_bind("tab: complete")
    except Exception:
        pass


def main():
    _install_completion()
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