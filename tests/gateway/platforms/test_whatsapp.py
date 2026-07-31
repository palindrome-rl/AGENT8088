import json, tempfile
from pathlib import Path


def test_whatsapp_adapter_imports():
    from agent8088.gateway.platforms.whatsapp import WhatsAppAdapter, WhatsAppStreamSink
    assert WhatsAppAdapter.platform == "whatsapp"
    assert WhatsAppStreamSink is not None


def test_whatsapp_markdown_conversion():
    from agent8088.gateway.platforms.whatsapp import markdown_to_whatsapp
    assert markdown_to_whatsapp("**bold**") == "*bold*"
    assert markdown_to_whatsapp("__bold__") == "*bold*"
    assert markdown_to_whatsapp("*italic*") == "_italic_"
    assert markdown_to_whatsapp("~~strike~~") == "~strike~"
    assert markdown_to_whatsapp("# Header") == "*Header*"
    assert markdown_to_whatsapp("### Sub") == "*Sub*"
    assert markdown_to_whatsapp("[text](url)") == "text (url)"
    assert "```" in markdown_to_whatsapp("```py\ncode\n```")
    assert "`inline`" in markdown_to_whatsapp("has `inline` code")


def test_whatsapp_adapter_reads_config_dict():
    from agent8088.gateway.platforms.whatsapp import WhatsAppAdapter
    config = {
        "whatsapp_bridge_port": "3001",
        "whatsapp_session_dir": str(Path.home() / ".local" / "share" / "agent8088" / "whatsapp" / "session"),
        "whatsapp_mode": "bot",
    }
    adapter = WhatsAppAdapter(config, runner=None)
    assert adapter.bridge_port == 3001
    assert adapter.bridge_url == "http://127.0.0.1:3001"


def test_whatsapp_allowlist_merge():
    from agent8088.gateway.auth import Allowlist
    config = {
        "whatsapp_allowed_users": "+923341490027",
        "slack_allowed_users": "U01ABC2DEF3",
    }
    al = Allowlist.from_config(config)
    assert al.is_allowed("+923341490027")
    assert al.is_allowed("U01ABC2DEF3")
    assert not al.is_allowed("U999ZZZ9ZZZ")


def test_normalize_whatsapp_id():
    from agent8088.gateway.auth import normalize_whatsapp_id
    assert normalize_whatsapp_id("923221540961@s.whatsapp.net") == "923221540961"
    assert normalize_whatsapp_id("23425456279692@lid") == "23425456279692"
    assert normalize_whatsapp_id("+923221540961") == "923221540961"


def test_expand_whatsapp_aliases():
    from agent8088.gateway.auth import expand_whatsapp_aliases
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        (session_dir / "lid-mapping-923221540961.json").write_text(json.dumps("12345"))
        (session_dir / "lid-mapping-12345_reverse.json").write_text(json.dumps("923221540961"))
        aliases = expand_whatsapp_aliases("12345", session_dir)
        assert "12345" in aliases
        assert "923221540961" in aliases


def test_allowlist_with_lid_resolution():
    from agent8088.gateway.auth import Allowlist
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        (session_dir / "lid-mapping-923221540961.json").write_text(json.dumps("23425456279692"))
        (session_dir / "lid-mapping-23425456279692_reverse.json").write_text(json.dumps("923221540961"))
        al = Allowlist(["+923221540961"], session_dir=session_dir)
        assert al.is_allowed("23425456279692")
        assert al.is_allowed("923221540961")
        assert not al.is_allowed("99999999999")


def test_allowlist_wildcard():
    from agent8088.gateway.auth import Allowlist
    al = Allowlist(["*"])
    assert al.is_allowed("923221540961")
    assert al.is_allowed("anyone")