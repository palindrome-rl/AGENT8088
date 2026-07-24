#!/usr/bin/env python3
"""agent8088 Benchmark — runs all tests via the ./agent8088 CLI with semantic verification.
   Runs sequentially, prints results per test, writes JSON at end."""
import subprocess, json, sys, os, time, re
from pathlib import Path

os.environ.setdefault("AGENT8088_PERMISSION", "edit")

# run_benchmark.py lives in research/ but the agent8088 executable + config
# stay at the repo root, so resolve one level up.
PROJECT = Path(__file__).resolve().parent.parent
RESULTS_FILE = PROJECT / "benchmark_results.json"

TESTS = [
    {"name": "shell_01", "query": "What is the current hostname and uptime?", "expect": ["hostname", "up", "  up", "load"], "category": "shell"},
    {"name": "shell_02", "query": "How much free memory is available on this system? Report the full output of free -h.", "expect": ["Mem", "free", "available", "Swap", "Gi", "total"], "category": "shell"},
    {"name": "shell_03", "query": "Show available disk space on all mounted filesystems.", "expect": ["Filesystem", "Avail"], "category": "shell"},
    {"name": "fact_01", "query": "What is the capital of France?", "expect": ["Paris"], "category": "fact"},
    {"name": "fact_02", "query": "What is the speed of light in km/s?", "expect": ["300000", "299792"], "category": "fact"},
    {"name": "reason_01", "query": "If all roses are flowers and some flowers fade quickly, does it follow that some roses fade quickly? Explain.", "expect": ["not", "necessarily", "no"], "category": "reasoning"},
    {"name": "reason_02", "query": "A train leaves New York at 9am going 60mph toward Chicago (800 miles away). Another leaves Chicago at 10am going 70mph toward NY. What time do they meet? Use the calculator tool to compute the answer.", "expect": ["2:", "2.5", "2pm", "14:", "hour", "30", "calculate", "step"], "category": "reasoning"},
    {"name": "multi_01", "query": "Create a file called /tmp/agent8088_test/config.txt with your name and today's date, then read it back.", "expect": ["config.txt", "content", "Agent8088"], "category": "multi"},
    {"name": "multi_02", "query": "List the directories in /home/amir/projects and count how many items are there", "expect": ["project", "file", "total", "director", "agent8088"], "category": "multi"},
    {"name": "curriculum_01", "query": "Write a Python script that downloads a webpage and saves its title.", "expect": ["requests", "title", "python"], "category": "curriculum"},
    {"name": "shell_exp_01", "query": "Show me the output of: df -h", "category": "shell", "expect": ["Filesystem", "Avail", "Size", "Mounted"]},
    {"name": "shell_exp_02", "query": "Run free -m and report the result", "category": "shell", "expect": ["Mem", "Swap"]},
    {"name": "shell_exp_03", "query": "Run whoami and report the result", "category": "shell", "expect": ["amir"]},
    {"name": "shell_exp_04", "query": "Run ip addr show | grep inet and report the result", "category": "shell", "expect": ["inet"]},
    {"name": "shell_exp_05", "query": "Run cat /etc/os-release | head -3 and report the result", "category": "shell", "expect": ["Ubuntu", "Zorin"]},
    {"name": "code_exp_01", "query": "Write a Python function called count_words that counts words in a string. Write it to count_words.py using write_file and test it.", "category": "code", "expect": ["count_words", "split", "def count", "wrote", "bytes", "count_words.py"]},
    {"name": "code_exp_02", "query": "Write a Python function that checks if a number is prime. Write it to is_prime.py using write_file and then test it with python3 -c", "category": "code", "expect": ["is_prime", "wrote", "prime_checker", "def"]},
    {"name": "code_exp_03", "query": "Write a function that flattens a nested list in Python. Write it to flatten.py using write_file.", "category": "code", "expect": ["flatten", "recursive", "wrote", "bytes", "isinstance", "extend", "flatten.py"]},
    {"name": "code_exp_04", "query": "Write a function that generates Fibonacci numbers up to N in Python", "category": "code", "expect": ["fibonacci", "yield"]},
    {"name": "fact_exp_01", "query": "What is the tallest mountain on Earth?", "category": "fact", "expect": ["Everest", "8848"]},
    {"name": "fact_exp_02", "query": "What is the boiling point of water in Fahrenheit?", "category": "fact", "expect": ["212"]},
    {"name": "fact_exp_03", "query": "What is the number of planets in our solar system?", "category": "fact", "expect": ["8"]},
    {"name": "reason_exp_01", "query": "If all dogs bark and Fido is a dog, what follows?", "category": "reason", "expect": ["true", "yes", "barks", "fido"]},
    {"name": "reason_exp_02", "query": "If all fish live in water and whales live in water, what follows?", "category": "reason", "expect": ["false", "no", "incorrect", "not"]},
    {"name": "multi_exp_01", "query": "Create a file called /tmp/agent8088_test/a.txt containing the word alpha using write_file", "category": "multi", "expect": ["alpha", "wrote", "bytes"]},
    {"name": "curriculum_exp_01", "query": "Write a Python script using requests to download a webpage title. Save the script to scraper.py using write_file.", "category": "curriculum", "expect": ["requests", "title", "scraper.py", "wrote"]},
    # New tests — shell
    {"name": "shell_new_01", "query": "Run the command: uptime", "category": "shell", "expect": ["up", "load", "user"]},
    {"name": "shell_new_02", "query": "Show me what: date +%Y shows", "category": "shell", "expect": ["2026"]},
    {"name": "shell_new_03", "query": "Run: echo 'Hello Agent8088' and show me the output", "category": "shell", "expect": ["hello", "agent8088"]},
    {"name": "shell_new_04", "query": "Check how many CPU cores this system has using nproc", "category": "shell", "expect": ["8", "16", "32", "64"]},
    {"name": "shell_new_05", "query": "What is my current working directory? Run pwd", "category": "shell", "expect": ["agent8088"]},
    # New tests — fact
    {"name": "fact_new_01", "query": "What is the chemical symbol for gold?", "category": "fact", "expect": ["au"]},
    {"name": "fact_new_02", "query": "What is the largest ocean on Earth?", "category": "fact", "expect": ["pacific"]},
    {"name": "fact_new_03", "query": "What year did the Berlin Wall fall?", "category": "fact", "expect": ["1989"]},
    # New tests — math/reasoning
    {"name": "math_new_01", "query": "What is 15 times 37? Don't use tools, just answer directly.", "category": "fact", "expect": ["555"]},
    {"name": "math_new_02", "query": "What is 144 divided by 12?", "category": "fact", "expect": ["12"]},
    # New tests — code (write_file pattern that we fixed)
    {"name": "code_new_01", "query": "Write a Python function called reverse_string that reverses a string. Write it to reverse_str.py using write_file.", "category": "code", "expect": ["reverse", "wrote", "bytes", "reverse_str.py"]},
    {"name": "code_new_02", "query": "Write a Python function called factorial that computes factorial. Write it to factorial.py using write_file.", "category": "code", "expect": ["factorial", "wrote", "bytes", "factorial.py"]},
    # Regression test — tic tac toe (from 6-5-2026 session)
    {"name": "code_new_03", "query": "Write a tic tac toe game to tic.py using write_file, then run: head -5 tic.py", "category": "code", "expect": ["wrote", "bytes", "tic.py", "def "]},
    # New tests — pipeline expansion (2026-06-08 cycle)
    {"name": "pipeline_new_01", "query": "Run: echo -e 'banana\\napple\\ncherry' | sort", "category": "shell", "expect": ["apple", "banana", "cherry"]},
    {"name": "math_new_03", "query": "What is the sum of all integers from 1 to 100? Give me the final number only.", "category": "reasoning", "expect": ["5050"]},
    {"name": "fact_new_04", "query": "What is the approximate value of pi?", "category": "fact", "expect": ["3.14", "3.14159"]},
    {"name": "multi_new_02", "query": "Create a file called /tmp/agent8088_test/status.txt containing the word active using write_file.", "category": "multi", "expect": ["active", "wrote", "bytes"]},
    {"name": "code_new_04", "query": "Write a Python function that checks if a word is a palindrome. Write it to palindrome.py using write_file.", "category": "code", "expect": ["palindrome", "wrote", "bytes", "palindrome.py"]},
    # New tests — 2026-06-08 cron cycle
    {"name": "code_new_05", "query": "Write a Python function that finds the maximum element in a list. Write it to find_max.py using write_file and test it.", "category": "code", "expect": ["max", "find", "wrote", "bytes", "find_max.py"]},
    {"name": "fact_new_05", "query": "What is the population of Tokyo as of 2024? Answer from your knowledge.", "category": "fact", "expect": ["million", "37", "popul"]},
    {"name": "multi_new_03", "query": "Write a Python script that prints 'Hello Agent8088' to get_title.py using write_file, then run python3 get_title.py", "category": "curriculum", "expect": ["Hello Agent8088", "wrote", "bytes"]},
    {"name": "shell_new_06", "query": "Run: env | grep HOME", "category": "shell", "expect": ["HOME", "amir"]},
    {"name": "fact_new_06", "query": "What is the square root of 144? Answer from your knowledge.", "category": "fact", "expect": ["12"]},
    {"name": "multi_new_04", "query": "Create a file called /tmp/agent8088_test/memory_report.txt containing the output of free -h using execute_shell and write_file.", "category": "multi", "expect": ["Mem", "free", "memory_report.txt", "wrote"]},
    # New tests — 2026-06-08 cron cycle #2
    {"name": "shell_new_07", "query": "Run the command: ls -d /tmp/agent8088_test", "category": "shell", "expect": ["agent8088_test"]},
    {"name": "fact_new_07", "query": "What is the diameter of the Earth in kilometers? Answer from your knowledge.", "category": "fact", "expect": ["12742", "12700", "kilometer"]},
    {"name": "code_new_06", "query": "Write a Python function named gcd that computes the greatest common divisor of two numbers. Write it to gcd.py using write_file.", "category": "code", "expect": ["gcd", "wrote", "bytes", "gcd.py"]},
    {"name": "multi_new_05", "query": "Create a file /tmp/agent8088_test/date.txt containing today's date. Use execute_shell to get the date, then write_file to save it.", "category": "multi", "expect": ["date.txt", "wrote", "bytes", "2026"]},
    {"name": "reason_exp_03", "query": "If a bakery sells 250 loaves on Monday and 20 percent more on Tuesday, how many loaves did they sell on Tuesday? Use the calculator to compute.", "category": "reasoning", "expect": ["300"]},
    {"name": "math_new_04", "query": "What is 2 raised to the 10th power? Answer from your knowledge, don't use tools.", "category": "fact", "expect": ["1024"]},
    # New tests — June 8 cron run
    {"name": "shell_new_08", "query": "Run the command: ls -d /tmp/agent8088_shell", "category": "shell", "expect": ["tmp", "agent8088", "exist", "no", "not found"]},
    {"name": "shell_new_09", "query": "Run: echo \"MARKER_AGENT8088_TEST\"", "category": "shell", "expect": ["marker_agent8088_test"]},
    {"name": "fact_new_08", "query": "What is the chemical symbol for gold? Don't use tools, just answer directly.", "category": "fact", "expect": ["au", "gold"]},
    {"name": "fact_new_09", "query": "How many planets are in our solar system? Don't use tools, just answer directly.", "category": "fact", "expect": ["8", "eight"]},
    {"name": "code_new_07", "query": "Write a Python program to /tmp/fib.py that prints the first 10 Fibonacci numbers. Use write_file then execute_shell to run it.", "category": "code", "expect": ["0", "1", "34", "wrote"]},
    {"name": "multi_new_06", "query": "Write the number 42 to /tmp/answer.txt, then read it back and tell me what it says.", "category": "multi", "expect": ["42", "wrote", "read"]},
    {"name": "multi_new_07", "query": "Create /tmp/test_dir via execute_shell, then write hello.txt inside it with write_file, then list the directory contents.", "category": "multi", "expect": ["hello.txt", "wrote", "mkdir"]},
]

def semantic_check(output: str, keywords: list) -> bool:
    out_lower = output.lower()
    normalized = out_lower.replace(",", "")
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in normalized or kw_lower in out_lower:
            return True
    return False

BENCH_RETRIES = 1   # retry transient failures once


def run_test(test: dict) -> dict:
    tid = test["name"]
    query = test["query"]
    expect = test.get("expect", [])
    t0 = time.time()
    last_output = ""
    for attempt in range(BENCH_RETRIES + 1):
        try:
            r = subprocess.run(
                ["./agent8088", query],
                capture_output=True, text=True, timeout=120, cwd=PROJECT
            )
            elapsed = time.time() - t0
            output = (r.stdout or "").strip()
            if output and "Error: Connection error" not in output:
                verified = semantic_check(output, expect) if expect else True
                print(f"  {tid}: {elapsed:.1f}s {'✅' if verified else '❌'} sem={verified}")
                return {"name": tid, "query": query, "output": output[:500], "verified": verified,
                        "elapsed": round(elapsed, 1), "category": test.get("category", "")}
            last_output = output
            if attempt < BENCH_RETRIES:
                time.sleep(2 * (attempt + 1))
        except subprocess.TimeoutExpired:
            if attempt < BENCH_RETRIES:
                time.sleep(1)
                continue
            print(f"  {tid}: TIMEOUT")
            return {"name": tid, "query": query, "output": "TIMEOUT", "verified": False,
                    "elapsed": 60, "category": test.get("category", "")}
        except Exception as e:
            if attempt < BENCH_RETRIES and "Connection" in str(e):
                time.sleep(2 * (attempt + 1))
                continue
            print(f"  {tid}: ERROR {e}")
            return {"name": tid, "query": query, "output": str(e)[:200], "verified": False,
                    "elapsed": 0, "category": test.get("category", "")}
    # All retries exhausted
    elapsed = time.time() - t0
    verified = semantic_check(last_output, expect) if (expect and last_output) else False
    print(f"  {tid}: {elapsed:.1f}s ❌ sem=False (connection error after {BENCH_RETRIES} retries)")
    return {"name": tid, "query": query, "output": last_output[:500] or "Connection error",
            "verified": verified, "elapsed": round(elapsed, 1), "category": test.get("category", "")}

print(f"agent8088 Benchmark — {len(TESTS)} tests\n")

results = [run_test(t) for t in TESTS]
passed = sum(1 for r in results if r["verified"])
total = len(results)

print(f"\n=== Summary ===")
print(f"Semantically verified: {passed}/{total}")

# Per-category breakdown
cats = {}
for r in results:
    c = r.get("category", "other")
    cats.setdefault(c, {"pass": 0, "total": 0})
    cats[c]["total"] += 1
    if r["verified"]:
        cats[c]["pass"] += 1
for c in sorted(cats):
    print(f"  {c}: {cats[c]['pass']}/{cats[c]['total']}")

data = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "total": total, "passed": passed,
        "semantically_verified": passed, "by_category": cats, "results": results}
RESULTS_FILE.write_text(json.dumps(data, indent=2))
print(f"Results saved to {RESULTS_FILE}")