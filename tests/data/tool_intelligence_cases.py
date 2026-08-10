"""Prompt -> expected tool behaviour, shared by the scripted and live harnesses.

One table, two readers. The scripted suite asserts the deterministic half
(does a relative-time query come out date-qualified?). The live harness in
scripts/verify_tool_intelligence.py asks the harder question a fake model
cannot answer: given this prompt, does the real model reach for the right tool
at all?

Expectations:
  no_tool          answer directly; calling any tool is a failure
  web_search       search, and don't pile a fetch on top of it
  web_search+year  search with the current year in the query
  web_search+month search with the current month and year in the query
  <tool name>      that specific tool is the right choice
"""

NO_TOOL = "no_tool"
SEARCH = "web_search"
SEARCH_YEAR = "web_search+year"
SEARCH_MONTH = "web_search+month"

CASES = [
    # --- nothing external is needed -------------------------------------
    ("hello there", NO_TOOL),
    ("summarize this in one line: the cat sat on the mat and then slept", NO_TOOL),
    ("translate 'good morning' into French", NO_TOOL),
    ("what is a python list comprehension", NO_TOOL),
    (("rewrite this to be shorter: we regret to inform you that the meeting "
      "has unfortunately been cancelled"), NO_TOOL),
    ("who wrote Pride and Prejudice", NO_TOOL),
    ("explain the difference between a list and a tuple", NO_TOOL),
    ("my API key is abc123 — what format is that", NO_TOOL),

    # --- current or externally verifiable --------------------------------
    ("who is the current UK prime minister", SEARCH_YEAR),
    ("what is the latest stable python release", SEARCH_YEAR),
    ("when is the next SpaceX launch", SEARCH_YEAR),
    ("recommend a good mechanical keyboard", SEARCH),
    ("what are today's top technology headlines", SEARCH_MONTH),
    ("what happened this week in AI", SEARCH_MONTH),

    # --- historical: pin the period, don't drift to the present ----------
    ("who won the 1998 football world cup final", SEARCH),
    ("what were the main causes of the 2008 financial crisis", SEARCH),

    # --- a specific tool is the right answer -----------------------------
    ("what is 17 * 23 + 4", "calculate"),
    ("read the file artifacts/tests/sample.txt", "read_text"),
    ("run `ls -la` in this directory", "execute_shell"),
]

# Cases whose outbound query must carry a date, checked deterministically.
DATE_CASES = [(prompt, expectation) for prompt, expectation in CASES
              if expectation in (SEARCH_YEAR, SEARCH_MONTH)]
