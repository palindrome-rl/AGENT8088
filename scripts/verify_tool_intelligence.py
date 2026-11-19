#!/usr/bin/env python3
"""Score a real model on tool choice.

The unit suite can prove a query leaves with the year attached. It cannot
prove the model decides to search in the first place, or that it resists
searching for something it already knows — that is judgement, and only a real
model has any. This runs the shared scenario table against the configured
provider and reports what it actually did.

Opt-in and sandboxed. It refuses to start without A8088_LIVE_MODEL=1, and it
always points AGENT8088_HOME at a throwaway directory so it can never read or
write a real ~/.agent8088.

    A8088_LIVE_MODEL=1 uv run --extra dev python scripts/verify_tool_intelligence.py

Exits non-zero below the pass threshold, so it can gate a release once the
baseline is known. Keep it out of the default pytest run: it costs tokens and
is not deterministic.
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tests.data.tool_intelligence_cases import (
    CASES,
    NO_TOOL,
    SEARCH,
    SEARCH_MONTH,
    SEARCH_YEAR,
)

THRESHOLD = float(os.environ.get("A8088_TOOL_THRESHOLD", "0.8"))


def _guard_environment():
    """Refuse to run against anyone's real configuration."""
    if os.environ.get("A8088_LIVE_MODEL") != "1":
        sys.exit("Refusing to run: set A8088_LIVE_MODEL=1 to spend real tokens.")
    home = os.environ.get("AGENT8088_HOME")
    if not home:
        home = tempfile.mkdtemp(prefix="a8088-live-")
        os.environ["AGENT8088_HOME"] = home
        print(f"AGENT8088_HOME not set — using throwaway {home}")
    real = Path.home() / ".agent8088"
    if Path(home).resolve() == real.resolve():
        sys.exit("Refusing to run against your real ~/.agent8088.")


BACKEND_ERROR_MARKERS = ("The model backend errored", "<EXCEPTION")

REFUSALS = ("Error:", "ESCALATION_REQUEST\x1f", "Follow-up fetch was not run",
            "This search already ran", "already ran with this output")


def _refused(result: str) -> bool:
    """Whether the engine stopped this call rather than letting it run."""
    return any(str(result).startswith(marker) or marker in str(result)[:80]
               for marker in REFUSALS)


def _judge(expectation, calls, moment, answer=""):
    """Return (passed, note) for one case.

    `calls` is the list of (name, arguments) that actually RAN — not what the
    model asked for. The distinction matters: an unsolicited browse_page that
    the engine refused is the gate working, and scoring the request as a
    failure would report a working guard as broken.

    A backend error fails every case. Without this check a run where every
    single call 401s scores the no-tool cases as passes, because the model
    "called no tools" — a broken provider reads as a well-behaved agent.
    """
    if any(marker in (answer or "") for marker in BACKEND_ERROR_MARKERS):
        return False, f"backend error: {answer[:80]}"

    names = [name for name, _args in calls]

    if expectation == NO_TOOL:
        return (not names), f"called {names}" if names else "answered directly"

    if expectation in (SEARCH, SEARCH_YEAR, SEARCH_MONTH):
        if "web_search" not in names:
            return False, f"did not search (called {names or 'nothing'})"
        query = next(str(a.get("query", "")) for n, a in calls if n == "web_search")
        # A fetch on top of a successful search is the redundancy we're hunting.
        extra = [n for n in names if n in {"browse_page", "get_page_title", "execute_shell"}]
        if extra:
            return False, f"piled {extra} onto a search"
        if expectation == SEARCH_YEAR and str(moment.year) not in query:
            return False, f"query lacks the year: {query!r}"
        if expectation == SEARCH_MONTH and moment.strftime("%B") not in query:
            return False, f"query lacks the month: {query!r}"
        return True, f"searched: {query!r}"

    ok = expectation in names
    return ok, f"called {names or 'nothing'}"


def main():
    _guard_environment()
    from agent8088 import engine as A

    moment = datetime.now().astimezone()
    results = []

    for prompt, expectation in CASES:
        calls = []
        asked = {}
        if expectation == "execute_shell" and A._resolve_sandbox_backend() in {"local", "unavailable"}:
            A.grant_escalation("local_execution")

        def _on_result(name, result, _sink=calls, _asked=asked):
            # Record what RAN. A refusal means the engine stopped it, which is
            # the guard doing its job, not a tool the model got to use.
            if _refused(result):
                return
            _sink.append((name, _asked.get(name, {})))

        def _on_calls(made, _asked=asked):
            for call in made:
                _asked[call["name"]] = call.get("arguments", {})

        try:
            answer = A.run_agent([{"role": "user", "content": prompt}], max_turns=3,
                                 on_calls=_on_calls, on_result=_on_result)
        except Exception as exc:                       # noqa: BLE001 - report, don't crash the sweep
            results.append((prompt, expectation, False, f"error: {exc}"))
            continue
        passed, note = _judge(expectation, calls, moment, answer)
        results.append((prompt, expectation, passed, note))
        print(f"{'PASS' if passed else 'FAIL'}  {expectation:<17} {prompt[:52]:<54} {note}")

    passed = sum(1 for *_x, ok, _n in results if ok)
    rate = passed / len(results) if results else 0.0

    out = ROOT / "artifacts" / f"tool-intelligence-{moment:%Y%m%d-%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Tool-intelligence run {moment:%Y-%m-%d %H:%M}", "",
             f"Provider: {A.ACTIVE_PROVIDER} / {A.MODEL_NAME}",
             f"Pass rate: {passed}/{len(results)} ({rate:.0%}), threshold {THRESHOLD:.0%}",
             "", "| Result | Expected | Prompt | What happened |", "|---|---|---|---|"]
    lines += [f"| {'PASS' if ok else 'FAIL'} | {exp} | {p} | {note} |"
              for p, exp, ok, note in results]
    out.write_text("\n".join(lines) + "\n")

    print(f"\n{passed}/{len(results)} passed ({rate:.0%}); threshold {THRESHOLD:.0%}")
    print(f"report: {out}")
    return 0 if rate >= THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
