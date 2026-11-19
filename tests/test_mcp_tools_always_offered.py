"""Every connected MCP tool is offered, not just the best-ranked one.

Issue sheet row 48: the mail server's forward tool ranked in, its list and read
tools ranked out, and the model replaced the working server via add_mcp_server.
"""
import pytest

MAIL = {
    "mcp_email_forward_email": "Forward an email message to another address.",
    "mcp_email_list_emails": "List messages in a mailbox folder.",
    "mcp_email_read_email": "Read one message by id.",
}


class _Embedder:
    def embed(self, texts):
        return [[1.0, 0.0] if "forward" in text.lower() else [0.0, 1.0] for text in texts]

    def embed_one(self, _query):
        return [1.0, 0.0]


@pytest.fixture
def mail_server(engine, monkeypatch):
    specs = dict(engine.TOOL_SPECS)
    for name, description in MAIL.items():
        specs[name] = {"mode": "mcp", "description": description, "args": []}
    monkeypatch.setattr(engine, "TOOL_SPECS", specs)
    engine._TOOL_INDEX_CACHE.clear()
    yield set(specs)
    engine._TOOL_INDEX_CACHE.clear()


@pytest.mark.parametrize("embedder", [None, _Embedder()], ids=["keyword", "hybrid"])
def test_a_server_is_offered_whole(engine, monkeypatch, mail_server, embedder):
    monkeypatch.setattr(engine.memory, "embedder", lambda: embedder)
    chosen = engine.select_tool_names_for_request(
        "forward the invoice to finance", mail_server, mode="hybrid")
    assert set(MAIL) <= chosen
    assert len(chosen) < len(mail_server)  # selection still narrows built-in tools


def test_mcp_tools_outside_the_allowlist_stay_hidden(engine, monkeypatch, mail_server):
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    allowed = mail_server - {"mcp_email_read_email"}
    chosen = engine.select_tool_names_for_request(
        "forward the invoice to finance", allowed, mode="hybrid")
    assert "mcp_email_read_email" not in chosen
    assert {"mcp_email_forward_email", "mcp_email_list_emails"} <= chosen
