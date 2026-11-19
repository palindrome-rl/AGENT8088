"""Golden set for tool-selection coverage.

Each entry is (label, prompt, required_tools), where required_tools is the
minimum set a competent agent must be able to call to finish the task. The first
entry's set is empirical: it is exactly what the model called when it was given
the full catalogue on a live run against ornith-1.0-35b.

Keep single-domain and compound prompts both represented. Ranked selection
passes every single-domain prompt and failed every compound one, so a set skewed
either way hides the regression this file exists to catch.
"""

GOLDEN = [
    ("compound_hw_git_web_write",
     "Check what local hardware I have available, then look at the last 5 git "
     "commits in this repo, then search the web for the latest agent8088 "
     "release notes, then write a short summary file combining all three to "
     "summary.md.",
     {"check_local_hardware", "git_log", "web_search", "write_file"}),

    ("read_then_verify_commits",
     "Read the CLAUDE.md file in this repo, extract the commit message rules, "
     "then check if the last 3 commits comply with those rules using git log.",
     {"read_text", "git_log"}),

    ("feature_add_document_test",
     "Add a new CLI command that prints the current sandbox status in JSON "
     "format, and make sure it's documented and tested.",
     {"read_text", "write_file", "execute_shell"}),

    ("debug_failing_tests",
     "This repository's tests are failing. Investigate why, and fix the "
     "underlying issue.",
     {"execute_shell", "read_text", "write_file"}),

    ("research_and_report",
     "Research the current state of open-source local LLM inference engines, "
     "compare llama.cpp, vLLM, and MLX for Apple Silicon, and produce a "
     "comparison table in a file.",
     {"web_search", "write_file"}),

    ("branch_commit_push",
     "Create a branch called feat/tidy, commit the current changes with a "
     "sensible message, and push it.",
     {"git_branch", "git_commit", "git_push"}),

    ("hardware_then_pull_model",
     "Work out what local models my machine can run, then pull the best one "
     "that fits.",
     {"check_local_hardware", "list_local_models", "pull_local_model"}),

    ("scrape_then_save",
     "Open the python.org downloads page, find the latest stable version "
     "number, and save it to a file called version.txt",
     {"browse_page", "write_file"}),

    ("doc_convert_pipeline",
     "Convert the report.docx in this folder to markdown and then read it back "
     "to me.",
     {"convert_document", "document_read"}),

    ("mcp_audit",
     "List the MCP servers I have connected, then remove any that aren't "
     "responding.",
     {"list_mcp_servers", "remove_mcp_server"}),
    ("repository_orientation",
     "I have never seen this codebase before. Survey the repository structure "
     "and tell me where the authentication code lives.",
     {"repository_read"}),
    ("code_review_then_fix",
     "Review the changes on this branch for bugs, then fix the worst one and prove the fix works.",
     {"review_code", "edit_file", "run_tests"}),
]
