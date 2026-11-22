"""Shared pieces of the Agent8088 benchmark: tasks, run facts, the harness verdict and the scores.

A benchmark run gives two answers about the same work: what the harness said
(its final answer and verdict) and what an independent grader found. Scoring one
against the other gives the two numbers this benchmark exists for:

* false done: the harness called the work done, the grader failed it
* false not verified: the harness doubted the work, the grader passed it

Everything here is plain functions over JSON, so it can be tested without a model.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

TASK_FIELDS = ("id", "kind", "prompt", "grader", "budget_sec")
TASK_KINDS = ("sandbox", "long")

# Trace step types that mark a problem in the run, and the ones that mark the
# harness getting the run going again after one.
PROBLEM_STEPS = frozenset({
    "length_cutoff", "tool_arg_parse_error", "tool_arg_cut_off", "unknown_tool", "missing_tool_args",
    "model_error", "empty_answer", "max_tokens", "stall_stop", "final_round_no_tools",
})
RECOVERY_STEPS = frozenset({
    "context_overflow_recovery", "cutoff_write_salvaged", "retry_mode", "model_escalation", "denial_breaker",
})

_LEVEL_RE = re.compile(r"\bVerification:\s*(tested|exercised|inspected|unchecked|failed)\b", re.IGNORECASE)
HARNESS_VERDICTS = ("verified", "done_unchecked", "not_verified", "no_answer")
# Verdicts in which the harness tells the user the work is done.
DONE_VERDICTS = ("verified", "done_unchecked")


# ----------------------------------------------------------------- tasks

def load_task(task_dir) -> dict:
    """Read and check one task folder's task.json."""
    task_dir = Path(task_dir)
    data = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    missing = [field for field in TASK_FIELDS if field not in data]
    if missing:
        raise ValueError(f"{task_dir.name}/task.json is missing {', '.join(missing)}")
    if data["id"] != task_dir.name:
        raise ValueError(f"task id {data['id']!r} does not match its folder {task_dir.name!r}")
    if data["kind"] not in TASK_KINDS:
        raise ValueError(f"{data['id']}: kind must be one of {', '.join(TASK_KINDS)}")
    if not (isinstance(data["budget_sec"], (int, float)) and data["budget_sec"] > 0):
        raise ValueError(f"{data['id']}: budget_sec must be a positive number")
    data["dir"] = task_dir
    data.setdefault("fixture", "")
    data.setdefault("needs", [])
    data.setdefault("services", [])
    for service in data["services"]:
        command, ports = service.get("command"), service.get("ports")
        if not (isinstance(command, list) and command and all(isinstance(part, str) for part in command)):
            raise ValueError(f"{data['id']}: a service needs a command, a list of strings")
        if not (isinstance(ports, list) and ports and all(isinstance(p, int) and 0 < p < 65536 for p in ports)):
            raise ValueError(f"{data['id']}: a service needs ports, a list of port numbers")
    return data


def discover_tasks(root) -> list:
    root = Path(root)
    return [load_task(folder) for folder in sorted(root.iterdir()) if (folder / "task.json").is_file()]


# ----------------------------------------------------------------- run facts

def _steps(data):
    for turn in (data or {}).get("trace") or []:
        if isinstance(turn, dict):
            for step in turn.get("steps") or []:
                if isinstance(step, dict):
                    yield step


def _final_answer(data):
    last = None
    for step in _steps(data):
        if step.get("type") == "final_answer":
            last = step
    return last


def harness_verdict(answer: str, incomplete: bool, end_reason: str, level: str) -> str:
    """What the harness told the user, in one of HARNESS_VERDICTS."""
    if end_reason not in ("answered", "checks_failed") and not answer:
        return "no_answer"
    if incomplete or end_reason == "checks_failed" or answer.lstrip().upper().startswith("NOT VERIFIED") \
            or level == "failed":
        return "not_verified"
    if not answer:
        return "no_answer"
    return "verified" if level in ("tested", "exercised") else "done_unchecked"


def summarize_trace(data, end_reason: str = "unknown") -> dict:
    """The run facts the benchmark records, from an agent8088 trace export (None when there is none)."""
    tokens_in = tokens_out = 0
    for turn in (data or {}).get("trace") or []:
        usage = turn.get("usage") if isinstance(turn, dict) else None
        if isinstance(usage, dict):
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
    compactions = failed = calls = 0
    problems: dict = {}
    recoveries: dict = {}
    for step in _steps(data):
        kind = step.get("type")
        if kind == "compaction":
            compactions += 1
            failed += 0 if step.get("ok") else 1
        elif kind == "tool_calls":
            calls += len(step.get("tools") or [])
        if kind in PROBLEM_STEPS:
            problems[kind] = problems.get(kind, 0) + 1
        if kind in RECOVERY_STEPS:
            recoveries[kind] = recoveries.get(kind, 0) + 1
    final = _final_answer(data) or {}
    answer = str(final.get("content") or "")
    match = _LEVEL_RE.search(answer)
    level = match.group(1).lower() if match else ""
    return {
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        # No input counted but output there: the server sent no usage, so the output is a length estimate.
        "tokens_estimated": tokens_in == 0 and tokens_out > 0,
        "tool_calls": calls,
        "compactions": compactions,
        "compactions_failed": failed,
        "problems": problems,
        "recoveries": recoveries,
        "end_reason": end_reason,
        "verification_level": level,
        "harness_verdict": harness_verdict(answer, bool(final.get("incomplete")), end_reason, level),
        "final_answer_head": answer[:600],
    }


def cost_usd(tokens_in: int, tokens_out: int, profile: dict):
    """Cost from the profile's prices in US dollars per million tokens; None when it gives none."""
    if "price_in" not in profile and "price_out" not in profile:
        return None
    return (tokens_in * float(profile.get("price_in") or 0) + tokens_out * float(profile.get("price_out") or 0)) / 1e6


# ----------------------------------------------------------------- scoring

def outcome(result: dict) -> str:
    """correct | false_done | false_not_verified | no_answer | ungraded.

    no_answer: the run ended (time budget, model error, watchdog) without telling
    the user anything, so there is no verdict to score, whatever the grader found."""
    grader = result.get("grader") or {}
    if "passed" not in grader:
        return "ungraded"
    if result.get("harness_verdict") == "no_answer":
        return "no_answer"
    done = result.get("harness_verdict") in DONE_VERDICTS
    if done and not grader["passed"]:
        return "false_done"
    if result.get("harness_verdict") == "not_verified" and grader["passed"]:
        return "false_not_verified"
    return "correct"


def _rate(numerator: int, denominator: int):
    return numerator / denominator if denominator else None


def _group(results: list) -> dict:
    graded = [r for r in results if outcome(r) != "ungraded"]
    passed = [r for r in graded if r["grader"]["passed"]]
    said_done = [r for r in graded if r.get("harness_verdict") in DONE_VERDICTS]
    doubted = [r for r in graded if r.get("harness_verdict") == "not_verified"]
    compacted = [r for r in graded if int(r.get("compactions") or 0) > 0]
    costs = [r["cost_usd"] for r in results if r.get("cost_usd") is not None]
    scores = [float(r["grader"].get("score") or 0) for r in graded]
    return {
        "runs": len(results),
        "graded": len(graded),
        "success_rate": _rate(len(passed), len(graded)),
        "mean_score": sum(scores) / len(scores) if scores else None,
        "false_done_rate": _rate(sum(outcome(r) == "false_done" for r in said_done), len(said_done)),
        "false_not_verified_rate": _rate(sum(outcome(r) == "false_not_verified" for r in doubted), len(doubted)),
        "no_answer_rate": _rate(sum(outcome(r) == "no_answer" for r in graded), len(graded)),
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in results),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in results),
        "cost_usd": sum(costs) if costs else None,
        "elapsed_sec": sum(float(r.get("elapsed_sec") or 0) for r in results),
        "compactions": sum(int(r.get("compactions") or 0) for r in results),
        "success_after_compaction": f"{sum(r['grader']['passed'] for r in compacted)}/{len(compacted)}",
        "recoveries": sum(sum((r.get("recoveries") or {}).values()) for r in results),
    }


def aggregate(results: list) -> dict:
    """Rates per task and model, and over every run."""
    groups: dict = {}
    for result in results:
        groups.setdefault(f"{result.get('task')} | {result.get('model')}", []).append(result)
    return {"groups": {key: _group(rows) for key, rows in sorted(groups.items())},
            "overall": _group(results)}


# ----------------------------------------------------------------- the sandbox the run used

_BACKEND_RE = re.compile(r"sandbox using ([a-z-]+)", re.IGNORECASE)


def sandbox_backend(log_text: str) -> str:
    """docker | none | native, from the agent's own log.

    Agent8088 announces a fallback (Docker) and a missing sandbox, but says nothing
    when the native sandbox starts normally, so no notice means native."""
    match = _BACKEND_RE.search(log_text or "")
    if match:
        return match.group(1).lower()
    if "sandboxed shell and Python commands are refused" in (log_text or ""):
        return "none"
    return "native"


# ----------------------------------------------------------------- wall clock

def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clock(iso: str) -> str:
    """A recorded time as 12-hour clock time with its UTC offset, e.g. '12:09 AM (+05:00)'."""
    moment = datetime.fromisoformat(iso)
    offset = moment.strftime("%z")
    zone = f"{offset[:3]}:{offset[3:]}" if offset else "local"
    return f"{moment.strftime('%I:%M %p').lstrip('0')} ({zone})"
