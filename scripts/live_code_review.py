"""Exercise actual Agent8088 delegation with its configured provider credential.

Usage: python scripts/live_code_review.py --executable PATH
Creates an isolated Git fixture; never prints credentials or changes user config.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from agent8088 import engine as e

sys.stdout.reconfigure(encoding="utf-8")

parser = argparse.ArgumentParser()
parser.add_argument("--executable", required=True)
options = parser.parse_args()

with tempfile.TemporaryDirectory(prefix="agent8088-review-live-") as directory:
    root = Path(directory)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "discount.py").write_text(
        "def discounted_price(price, percent):\n"
        "    # percent is a number from 0 to 100.\n"
        "    return price * (1 - percent)\n", encoding="utf-8")
    e.APP_CONFIG["open_code_review_enabled"] = "1"
    e.APP_CONFIG["open_code_review_executable"] = options.executable
    e.ALLOWED_PATHS = [root]
    e.PROJECT_ROOT = root
    e.client, e.MODEL_NAME = e.get_client("ollama-cloud")
    e.MODEL_NAME = "glm-5.3"
    e.ACTIVE_PROVIDER = "ollama-cloud"
    e.PERMISSION_MODE = "full-auto"
    captured = []
    def result(name, output):
        captured.append(name)
        print("Tool:", name, flush=True)
    answer = e.run_agent([
        {"role": "user", "content": f"Use review_code on {root} to prepare a review, then read the selected source with read_text and review it. Report concrete bugs with file and line and coverage. Do not modify files. Do not merely report preparation as review completion."}],
        allowed_tools={"review_code", "read_text", "last_output", "describe_tool"},
        on_result=result, memory_capture=False)
    print(answer)
    assert "review_code" in captured
    assert "read_text" in captured
    assert "100" in answer
    print(json.dumps({"live": "passed", "model": e.MODEL_NAME, "tools": captured}))
