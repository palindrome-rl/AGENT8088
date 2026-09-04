from agent8088 import cli


def test_command_catalog_covers_cli_commands_and_aliases():
    catalog = {entry["name"]: entry for entry in cli.command_catalog()}

    assert set(cli.COMMANDS) <= set(catalog)
    assert catalog["search"]["usage"] == "/search [status|use|setup|stop]"
    assert catalog["mcp"]["usage"] == "/mcp [reload|add|remove]"
    assert catalog["mode"]["usage"] == "/mode [readonly|full-auto]"
    assert catalog["image"]["usage"] == "/image <path-or-url> [question]"
    assert catalog["think"]["description"] == "Alias for /reasoning"
    assert catalog["agents"]["usage"] == "/agents [new|edit|delete|models]"
    assert catalog["fusion"]["usage"].startswith("/fusion [setup|")
    assert catalog["task"]["usage"] == "/task [start|resume|end|output|list] <value>"
    assert catalog["exit"]["aliases"] == ["quit"]
