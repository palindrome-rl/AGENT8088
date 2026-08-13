def test_slack_adapter_imports():
    from agent8088.gateway.platforms.slack import SlackAdapter, SlackStreamSink, markdown_to_slack
    assert SlackAdapter.platform == "slack"
    assert SlackStreamSink is not None
    assert callable(markdown_to_slack)


def test_markdown_to_slack_bold():
    from agent8088.gateway.platforms.slack import markdown_to_slack
    assert markdown_to_slack("**bold**") == "*bold*"
    assert markdown_to_slack("__bold__") == "*bold*"


def test_markdown_to_slack_italic():
    from agent8088.gateway.platforms.slack import markdown_to_slack
    assert markdown_to_slack("*italic*") == "_italic_"


def test_markdown_to_slack_strike():
    from agent8088.gateway.platforms.slack import markdown_to_slack
    assert markdown_to_slack("~~strike~~") == "~strike~"


def test_markdown_to_slack_header():
    from agent8088.gateway.platforms.slack import markdown_to_slack
    assert markdown_to_slack("# Header") == "*Header*"
    assert markdown_to_slack("### Sub") == "*Sub*"


def test_markdown_to_slack_link():
    from agent8088.gateway.platforms.slack import markdown_to_slack
    assert markdown_to_slack("[text](url)") == "text (url)"


def test_markdown_to_slack_code_preserved():
    from agent8088.gateway.platforms.slack import markdown_to_slack
    assert "```" in markdown_to_slack("```py\ncode\n```")
    assert "`inline`" in markdown_to_slack("has `inline` code")


def test_slack_adapter_reads_config_dict(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.slack import SlackAdapter
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    config = {
        "slack_bot_token": "xoxb-test",
        "slack_app_token": "xapp-test",
        "slack_allowed_users": "U01ABC2DEF3",
    }
    adapter = SlackAdapter(config, runner=None)
    assert adapter.bot_token == "xoxb-test"
    assert adapter.app_token == "xapp-test"


def test_slack_make_stream_sink_passes_thread_ts():
    from agent8088.gateway.platforms.slack import SlackAdapter, SlackStreamSink
    config = {"slack_bot_token": "xoxb-t", "slack_app_token": "xapp-t"}
    adapter = SlackAdapter(config, runner=None)
    sink = adapter.make_stream_sink("C123", thread_ts="1700000.0")
    assert isinstance(sink, SlackStreamSink)
    assert sink.thread_ts == "1700000.0"