"""Smoke-test starts that say nothing about the script are not crashes.

Seen in one benchmark run, on a scraper that had just run correctly against the real server:
- a check script ran the scraper as a child process. The child failed to write its output in the read-only copy
  ("PermissionError: ... 'products.csv'"), the parent printed its own lines after it, and the start was reported as
  "the script crashed".
- the scraper itself joined its argument with a relative path, so `--help` was dropped and the error read
  "unknown url type: 'products/page1.html'".
Both are now "not verified". A real error in the code is still a crash.
"""
from agent8088 import app_check

CHILD = ("server up on 127.0.0.1:8765\nTraceback (most recent call last):\n  File \"scrape.py\", line 160, in main\n"
         "PermissionError: [Errno 13] Permission denied: 'products.csv'\nscrape exit code: 1\n"
         "--- products.csv ---\nCommand exited with status 1.")
DROPPED = ("Traceback (most recent call last):\n  File \"scrape.py\", line 189, in <module>\n"
           "ValueError: unknown url type: 'products/page1.html'\nCommand exited with status 1.")


def _start(tmp_path, output, name="run_check.py"):
    script = tmp_path / name
    script.write_text("print('x')\n", encoding="utf-8")
    soft = []
    found = app_check._run_briefly(script, lambda command, timeout: output, soft=soft)
    return found, soft


def test_a_child_error_about_the_read_only_copy_is_not_a_crash(tmp_path):
    found, soft = _start(tmp_path, CHILD)
    assert found == []
    assert soft and "read-only copy" in soft[0] and "PermissionError" in soft[0]


def test_a_dropped_help_argument_is_not_a_crash(tmp_path):
    found, soft = _start(tmp_path, DROPPED, "scrape.py")
    assert found == []
    assert soft == ["it took --help as its input when started with it, so its startup was not verified"]


def test_a_real_code_error_is_still_a_crash(tmp_path):
    output = ("Traceback (most recent call last):\n  File \"scrape.py\", line 3\nNameError: name 'parse' is not defined"
              "\nCommand exited with status 1.")
    found, soft = _start(tmp_path, output, "scrape.py")
    assert found and "NameError" in found[0]
    assert soft == []


def test_an_absolute_unknown_url_is_still_a_crash(tmp_path):
    output = ("Traceback (most recent call last):\n  File \"scrape.py\", line 9\n"
              "ValueError: unknown url type: 'htp://shop/products/page1.html'\nCommand exited with status 1.")
    found, _ = _start(tmp_path, output, "scrape.py")
    assert found
