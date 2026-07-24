#!/usr/bin/env python3
"""
SkillOpt — Text-Space Optimization for Agent8088 Skills

Implements the SkillOpt technique (arXiv:2605.23904) for agent8088.
Optimizes the system.md skill document through rollout evaluation,
optimizer-model edits, and validation gating — without touching model weights.

Usage:
  python3 skillopt.py                    # Run full optimization cycle
  python3 skillopt.py --epochs 4         # Override epoch count
  python3 skillopt.py --dry-run          # Propose edits without applying
  python3 skillopt.py --report           # Show optimization history
  python3 skillopt.py --restore          # Restore pre-optimization skill

Architecture:
  1. Rollout: Run benchmark suite, capture trajectories + scores
  2. Reflect: Optimizer model analyzes failures, proposes skill edits
  3. Validate: Run benchmark with edited skill, accept only if strictly better
  4. Repeat for N epochs with cosine-decaying textual learning rate

The target model (agent8088's qwen14b) runs tasks. A separate optimizer model
(can be bigger — configurable) proposes edits to system.md. The validation
gate ensures we never regress.
"""
import sys, os, json, subprocess, time, re, shutil, math
from pathlib import Path
from openai import OpenAI
from datetime import datetime

APP_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Config — reuses agent8088's config.txt for target model, adds optimizer config
# ---------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    config = {}
    if not path.exists():
        return config
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config

CONFIG = load_config(APP_DIR / "config.txt")

# Target model (the one being optimized)
TARGET_BASE_URL = CONFIG.get("model_base_url", os.environ.get("OLLAMA_URL", "http://localhost:11434/v1"))
TARGET_MODEL = CONFIG.get("model_name", "qwen14b-tooluse-v3")

# Optimizer model (the one that edits the skill doc) — can be different/bigger
OPTIMIZER_BASE_URL = CONFIG.get("optimizer_base_url", TARGET_BASE_URL)
OPTIMIZER_MODEL = CONFIG.get("optimizer_model", TARGET_MODEL)
OPTIMIZER_API_KEY = CONFIG.get("optimizer_api_key", CONFIG.get("api_key", "ollama"))

# Paths
SYSTEM_FILE = Path(CONFIG.get("system_file", str(APP_DIR / "system.md")))
BENCHMARK_FILE = APP_DIR / "run_benchmark.py"
BENCHMARK_RESULTS = APP_DIR / "benchmark_results.json"
SKILLOPT_DIR = APP_DIR / "skillopt_data"
SKILLOPT_DIR.mkdir(exist_ok=True)

# Hyperparameters (paper defaults)
DEFAULT_EPOCHS = int(CONFIG.get("skillopt_epochs", "4"))
ROLLOUT_BATCH = int(CONFIG.get("skillopt_rollout_batch", "40"))
REFLECTION_MINIBATCH = int(CONFIG.get("skillopt_reflection_minibatch", "8"))
TEXTUAL_LR = int(CONFIG.get("skillopt_textual_lr", "4"))  # max edits per epoch
VALIDATION_STRICT = True  # ties rejected, only strictly better accepted

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
class C:
    R = "\033[0m"; B = "\033[1m"; DIM = "\033[2m"
    RED = "\033[91m"; GREEN = "\033[92m"; YELLOW = "\033[93m"
    CYAN = "\033[96m"; GRAY = "\033[90m"; ORANGE = "\033[38;5;208m"


def cc(color, text, bold=False):
    return f"{C.B if bold else ''}{color}{text}{C.R}"


# ---------------------------------------------------------------------------
# Skill document management
# ---------------------------------------------------------------------------
def load_skill() -> str:
    if SYSTEM_FILE.exists():
        return SYSTEM_FILE.read_text()
    return ""


def save_skill(content: str, backup_name: str | None = None):
    """Save skill with backup."""
    if backup_name:
        backup = SKILLOPT_DIR / f"skill_{backup_name}.md"
        backup.write_text(content)
    SYSTEM_FILE.write_text(content)


def restore_pre_optimization():
    """Restore the oldest backup."""
    backups = sorted(SKILLOPT_DIR.glob("skill_epoch0_*.md"))
    if not backups:
        backups = sorted(SKILLOPT_DIR.glob("skill_pre_opt_*.md"))
    if backups:
        SYSTEM_FILE.write_text(backups[0].read_text())
        print(f"{cc(C.GREEN, '✓')} Restored skill from {backups[0].name}")
    else:
        print(f"{cc(C.RED, '✗')} No pre-optimization backup found")


# ---------------------------------------------------------------------------
# Benchmark runner (rollout phase)
# ---------------------------------------------------------------------------
def run_benchmark(label: str = "") -> dict:
    """Run the benchmark suite and return parsed results."""
    print(f"\n{cc(C.CYAN, '▶ Rollout:', bold=True)} {label}")
    t0 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, str(BENCHMARK_FILE)],
            capture_output=True, text=True, timeout=1200, cwd=str(APP_DIR)
        )
    except subprocess.TimeoutExpired:
        print(f"  {cc(C.RED, '✗ Benchmark timed out')}")
        return {"passed": 0, "total": 0, "score": 0.0, "results": []}

    # Parse results from JSON file (more reliable than stdout)
    if BENCHMARK_RESULTS.exists():
        data = json.loads(BENCHMARK_RESULTS.read_text())
        passed = data.get("passed", 0)
        total = data.get("total", 0)
        score = passed / total if total > 0 else 0.0
        results = data.get("results", [])
        elapsed = time.time() - t0
        print(f"  {cc(C.GREEN if score > 0.7 else C.YELLOW, f'{passed}/{total}', bold=True)} "
              f"({score:.1%}) in {elapsed:.0f}s")
        return {"passed": passed, "total": total, "score": score, "results": results,
                "elapsed": elapsed, "stdout": r.stdout}
    return {"passed": 0, "total": 0, "score": 0.0, "results": [], "stdout": r.stdout}


def extract_failures(results: list) -> list:
    """Extract failed test cases for the optimizer to analyze."""
    failures = []
    for r in results:
        if not r.get("verified", False):
            failures.append({
                "name": r.get("name", ""),
                "query": r.get("query", ""),
                "output": r.get("output", "")[:500],
                "category": r.get("category", ""),
            })
    return failures


def extract_successes(results: list) -> list:
    """Extract successful test cases."""
    return [{
        "name": r.get("name", ""),
        "query": r.get("query", ""),
        "output": r.get("output", "")[:300],
        "category": r.get("category", ""),
    } for r in results if r.get("verified", False)]


# ---------------------------------------------------------------------------
# Optimizer model — proposes skill edits
# ---------------------------------------------------------------------------
def get_optimizer_client():
    return OpenAI(base_url=OPTIMIZER_BASE_URL, api_key=OPTIMIZER_API_KEY, timeout=120)


def build_optimizer_prompt(skill_doc: str, failures: list, successes: list,
                            epoch: int, total_epochs: int, lr: int,
                            rejected_edits: list) -> str:
    """Build the prompt for the optimizer model to propose skill edits."""
    # Cosine-decayed learning rate → edit budget
    edit_budget = max(2, lr)

    failures_text = "\n".join(
        f"  FAIL [{f['category']}]: {f['query']}\n    Got: {f['output'][:200]}"
        for f in failures[:20]  # cap for context
    ) or "  (none — all tests passed)"

    successes_text = "\n".join(
        f"  PASS [{s['category']}]: {s['query']}"
        for s in successes[:15]
    ) or "  (none)"

    rejected_text = ""
    if rejected_edits:
        rejected_text = "\n\n## Previously Rejected Edits (do NOT repeat these)\n"
        for rej in rejected_edits[-5:]:
            rejected_text += f"- REJECTED: {rej[:200]}\n"

    return f"""You are a skill optimizer. Your job is to improve the agent's skill document based on rollout results.

## Current Skill Document
---
{skill_doc}
---

## Rollout Results (Epoch {epoch}/{total_epochs})

### Failed Tests:
{failures_text}

### Passed Tests:
{successes_text}
{rejected_text}

## Your Task
Analyze the failures and propose up to {edit_budget} atomic edits to the skill document.
Each edit must be one of:
- ADD: Append new instruction text to the skill
- DELETE: Remove a specific line or section (quote it exactly)
- REPLACE: Replace a specific line with improved text

## Rules
1. Focus on patterns across multiple failures, not single cases
2. Don't add tool-specific instructions (tools are auto-generated separately)
3. Keep instructions concise — the skill doc should be < 2000 words
4. Address the root cause, not the symptom
5. If a failure pattern is "model didn't use a tool", add an instruction about when to use tools
6. If a failure pattern is "wrong answer", add guidance about verifying answers

## Output Format
Return ONLY valid JSON, no other text:
{{
  "analysis": "Brief analysis of failure patterns",
  "edits": [
    {{
      "type": "ADD",
      "content": "New instruction text to append",
      "reason": "Why this helps"
    }},
    {{
      "type": "DELETE",
      "target": "Exact line to remove (must match exactly)",
      "reason": "Why this hurts"
    }},
    {{
      "type": "REPLACE",
      "target": "Exact line to replace (must match exactly)",
      "content": "Replacement text",
      "reason": "Why this is better"
    }}
  ]
}}
"""


def apply_edits(skill_doc: str, edits: list) -> tuple:
    """Apply edits to the skill document. Returns (new_doc, applied_count)."""
    lines = skill_doc.splitlines()
    new_lines = list(lines)
    applied = 0

    for edit in edits:
        etype = edit.get("type", "").upper()
        reason = edit.get("reason", "")

        if etype == "ADD":
            content = edit.get("content", "").strip()
            if content:
                new_lines.append("")
                new_lines.append(content)
                applied += 1
                print(f"  {cc(C.GREEN, '+ ADD')}: {reason}")

        elif etype == "DELETE":
            target = edit.get("target", "").strip()
            if target:
                for i, line in enumerate(new_lines):
                    if line.strip() == target:
                        new_lines.pop(i)
                        applied += 1
                        print(f"  {cc(C.RED, '- DELETE')}: {reason}")
                        break

        elif etype == "REPLACE":
            target = edit.get("target", "").strip()
            content = edit.get("content", "").strip()
            if target and content:
                for i, line in enumerate(new_lines):
                    if line.strip() == target:
                        new_lines[i] = content
                        applied += 1
                        print(f"  {cc(C.YELLOW, '~ REPLACE')}: {reason}")
                        break

    return "\n".join(new_lines), applied


def run_optimizer(skill_doc: str, failures: list, successes: list,
                  epoch: int, total_epochs: int, lr: int,
                  rejected_edits: list) -> tuple:
    """Run the optimizer model to get proposed edits. Returns (edits, raw_response)."""
    client = get_optimizer_client()
    prompt = build_optimizer_prompt(skill_doc, failures, successes, epoch,
                                     total_epochs, lr, rejected_edits)

    messages = [{"role": "user", "content": prompt}]  # type: ignore[list-item]

    print(f"\n{cc(C.CYAN, '▶ Reflection:', bold=True)} Epoch {epoch}/{total_epochs}, "
          f"LR={lr}, {len(failures)} failures, {len(successes)} successes")

    try:
        response = client.chat.completions.create(
            model=OPTIMIZER_MODEL,
            messages=messages,
            max_tokens=2000,
            temperature=0.3,
        )
        raw = response.choices[0].message.content.strip()

        # Extract JSON from response (handle markdown code fences)
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if json_match:
            try:
                result = json.loads(json_match.group(0))
                edits = result.get("edits", [])
                analysis = result.get("analysis", "")
                if analysis:
                    print(f"  {cc(C.GRAY, 'Analysis:')} {analysis[:200]}")
                return edits, raw
            except json.JSONDecodeError:
                print(f"  {cc(C.RED, '✗ Optimizer returned invalid JSON')}")
                return [], raw
        else:
            print(f"  {cc(C.RED, '✗ No JSON found in optimizer response')}")
            return [], raw

    except Exception as e:
        print(f"  {cc(C.RED, f'✗ Optimizer error: {e}')}")
        return [], ""


# ---------------------------------------------------------------------------
# Cosine learning rate schedule
# ---------------------------------------------------------------------------
def cosine_lr(epoch: int, total_epochs: int, base_lr: int, floor: int = 2) -> int:
    """Cosine decay schedule for textual learning rate."""
    if total_epochs <= 1:
        return max(floor, base_lr)
    progress = epoch / (total_epochs - 1)
    decayed = base_lr * (1 + math.cos(math.pi * progress)) / 2
    return max(floor, round(decayed))


# ---------------------------------------------------------------------------
# Validation gate
# ---------------------------------------------------------------------------
def validate_gate(current_score: float, candidate_score: float,
                  strict: bool = True) -> bool:
    """Accept candidate only if it strictly improves."""
    if strict:
        return candidate_score > current_score
    return candidate_score >= current_score


# ---------------------------------------------------------------------------
# Main optimization loop
# ---------------------------------------------------------------------------
def optimize(epochs: int = DEFAULT_EPOCHS, dry_run: bool = False):
    """Run the full SkillOpt optimization loop."""
    print(f"\n{cc(C.ORANGE, '╔══════════════════════════════════════════╗', bold=True)}")
    print(f"{cc(C.ORANGE, '║       SkillOpt — Text-Space Optimizer      ║', bold=True)}")
    print(f"{cc(C.ORANGE, '╚══════════════════════════════════════════╝', bold=True)}")
    print(f"  Target:   {cc(C.CYAN, TARGET_MODEL)} @ {TARGET_BASE_URL}")
    print(f"  Optimizer: {cc(C.CYAN, OPTIMIZER_MODEL)} @ {OPTIMIZER_BASE_URL}")
    print(f"  Epochs:   {epochs}")
    print(f"  System:   {SYSTEM_FILE}")
    print(f"  Strict:   {VALIDATION_STRICT}")
    print()

    # Phase 0: Baseline rollout + backup
    original_skill = load_skill()
    if not original_skill:
        print(f"{cc(C.RED, '✗ No system.md found — nothing to optimize')}")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_skill(original_skill, f"pre_opt_{timestamp}")
    print(f"{cc(C.GREEN, '✓')} Backed up original skill to skillopt_data/skill_pre_opt_{timestamp}.md")

    baseline = run_benchmark("Baseline")
    current_score = baseline["score"]
    best_score = current_score
    best_skill = original_skill

    print(f"\n{cc(C.CYAN, 'Baseline score:', bold=True)} {current_score:.1%} "
          f"({baseline['passed']}/{baseline['total']})")

    # Check if we even need optimization
    if current_score >= 1.0:
        print(f"\n{cc(C.GREEN, '✓ Already at 100% — no optimization needed')}")
        return

    # Track rejected edits for negative feedback buffer
    rejected_edits = []
    history = [{
        "epoch": 0, "score": current_score, "passed": baseline["passed"],
        "total": baseline["total"], "edits_applied": 0, "accepted": True
    }]

    # Phase 1-N: Optimization epochs
    for epoch in range(1, epochs + 1):
        lr = cosine_lr(epoch - 1, epochs, TEXTUAL_LR)
        print(f"\n{cc(C.ORANGE, f'══ Epoch {epoch}/{epochs} ', bold=True)}"
              f"{cc(C.GRAY, f'(textual LR={lr})')}")

        # Phase 1: Rollout with current skill
        rollout = run_benchmark(f"Epoch {epoch}")
        rollout_score = rollout["score"]
        rollout_results = rollout["results"]

        # Phase 2: Reflection — optimizer proposes edits
        failures = extract_failures(rollout_results)
        successes = extract_successes(rollout_results)

        if not failures:
            print(f"\n{cc(C.GREEN, '✓ All tests passed — convergence reached')}")
            break

        edits, raw_response = run_optimizer(
            best_skill, failures, successes, epoch, epochs, lr, rejected_edits
        )

        if not edits:
            print(f"  {cc(C.YELLOW, '⚠ No edits proposed — skipping epoch')}")
            history.append({
                "epoch": epoch, "score": rollout_score, "passed": rollout["passed"],
                "total": rollout["total"], "edits_applied": 0, "accepted": False,
                "reason": "no_edits"
            })
            continue

        # Phase 3: Apply edits
        candidate_skill, applied_count = apply_edits(best_skill, edits)

        if applied_count == 0:
            print(f"  {cc(C.YELLOW, '⚠ No edits could be applied')}")
            rejected_edits.extend([e.get("reason", str(e)) for e in edits])
            history.append({
                "epoch": epoch, "score": rollout_score, "passed": rollout["passed"],
                "total": rollout["total"], "edits_applied": 0, "accepted": False,
                "reason": "no_appliable_edits"
            })
            continue

        if dry_run:
            print(f"\n  {cc(C.GRAY, '[DRY RUN] Would apply')} {applied_count} edits")
            # Save candidate for inspection
            (SKILLOPT_DIR / f"candidate_epoch{epoch}.md").write_text(candidate_skill)
            continue

        # Phase 4: Validation gate — run benchmark with candidate skill
        SYSTEM_FILE.write_text(candidate_skill)
        validation = run_benchmark(f"Validation epoch {epoch}")
        candidate_score = validation["score"]

        accepted = validate_gate(best_score, candidate_score, VALIDATION_STRICT)

        if accepted:
            print(f"\n  {cc(C.GREEN, '✓ ACCEPTED', bold=True)} "
                  f"{candidate_score:.1%} > {best_score:.1%} "
                  f"({candidate_score - best_score:+.1%})")
            best_score = candidate_score
            best_skill = candidate_skill
            # Save as best
            (SKILLOPT_DIR / "best_skill.md").write_text(best_skill)
        else:
            print(f"\n  {cc(C.RED, '✗ REJECTED', bold=True)} "
                  f"{candidate_score:.1%} ≤ {best_score:.1%} — reverting")
            # Revert to best skill
            SYSTEM_FILE.write_text(best_skill)
            rejected_edits.extend([e.get("reason", str(e)) for e in edits])

        history.append({
            "epoch": epoch, "score": candidate_score, "passed": validation["passed"],
            "total": validation["total"], "edits_applied": applied_count,
            "accepted": accepted, "prev_score": best_score
        })

    # Phase 5: Finalize — ensure best skill is active
    SYSTEM_FILE.write_text(best_skill)
    (SKILLOPT_DIR / "best_skill.md").write_text(best_skill)
    (SKILLOPT_DIR / "optimization_history.json").write_text(json.dumps(history, indent=2))

    # Summary
    final_passed = history[-1].get("passed", baseline["passed"]) if history else baseline["passed"]
    final_total = history[-1].get("total", baseline["total"]) if history else baseline["total"]
    print(f"\n{cc(C.ORANGE, '═══════════════════════════════════════', bold=True)}")
    print(f"{cc(C.ORANGE, '  SkillOpt Complete', bold=True)}")
    print(f"{cc(C.ORANGE, '═══════════════════════════════════════', bold=True)}")
    print(f"  Baseline:  {cc(C.GRAY, f'{current_score:.1%}')} ({baseline['passed']}/{baseline['total']})")
    print(f"  Final:     {cc(C.GREEN, f'{best_score:.1%}', bold=True)} ({final_passed}/{final_total})")
    print(f"  Delta:     {cc(C.GREEN if best_score > current_score else C.RED, f'{best_score - current_score:+.1%}')}")
    print(f"  Best skill saved to: {cc(C.CYAN, str(SKILLOPT_DIR / 'best_skill.md'))}")
    print(f"  History:   {cc(C.CYAN, str(SKILLOPT_DIR / 'optimization_history.json'))}")
    print(f"  Active skill: {cc(C.CYAN, str(SYSTEM_FILE))}")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def show_report():
    """Show optimization history."""
    hist_file = SKILLOPT_DIR / "optimization_history.json"
    if not hist_file.exists():
        print(f"{cc(C.YELLOW, 'No optimization history found')}")
        return

    history = json.loads(hist_file.read_text())
    print(f"\n{cc(C.ORANGE, 'SkillOpt History', bold=True)}")
    print(f"{'Epoch':>6} {'Score':>8} {'Pass/Total':>12} {'Edits':>7} {'Accepted':>9}")
    print(f"{'─'*6} {'─'*8} {'─'*12} {'─'*7} {'─'*9}")
    for h in history:
        epoch = h.get("epoch", 0)
        score = h.get("score", 0)
        passed = h.get("passed", 0)
        total = h.get("total", 0)
        edits = h.get("edits_applied", 0)
        accepted = h.get("accepted", False)
        mark = cc(C.GREEN, "✓") if accepted else cc(C.RED, "✗")
        print(f"{epoch:>6} {score:>7.1%} {passed:>5}/{total:<6} {edits:>7} {mark:>9}")

    # Show best skill preview
    best_file = SKILLOPT_DIR / "best_skill.md"
    if best_file.exists():
        content = best_file.read_text()
        print(f"\n{cc(C.CYAN, 'Best Skill Preview (first 500 chars):', bold=True)}")
        print(f"{cc(C.GRAY, content[:500])}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    report_mode = "--report" in args
    restore_mode = "--restore" in args

    if report_mode:
        show_report()
        return

    if restore_mode:
        restore_pre_optimization()
        return

    epochs = DEFAULT_EPOCHS
    for i, arg in enumerate(args):
        if arg == "--epochs" and i + 1 < len(args):
            epochs = int(args[i + 1])

    optimize(epochs=epochs, dry_run=dry_run)


if __name__ == "__main__":
    main()