import json, tempfile
from pathlib import Path
from agent8088.gateway.auth import Allowlist, normalize_whatsapp_id, expand_whatsapp_aliases


def test_allowlist_membership():
    al = Allowlist(["+15551234567", "+15557654321"])
    assert al.is_allowed("+15551234567")
    assert not al.is_allowed("+15550000000")
    assert "+15557654321" in al


def test_allowlist_add_remove():
    al = Allowlist([])
    al.add("+15551234567")
    assert al.is_allowed("+15551234567")
    al.remove("+15551234567")
    assert not al.is_allowed("+15551234567")


def test_allowlist_from_config_dict():
    config = {
        "whatsapp_allowed_users": "+15551234567,+15557654321",
        "slack_allowed_users": "",
    }
    al = Allowlist.from_config(config)
    assert al.is_allowed("+15551234567")
    assert al.is_allowed("+15557654321")
    assert not al.is_allowed("+15550000000")


def test_allowlist_empty_when_config_missing():
    al = Allowlist.from_config({})
    assert not al.is_allowed("anyone")


def test_normalize_whatsapp_id():
    assert normalize_whatsapp_id("923221540961@s.whatsapp.net") == "923221540961"
    assert normalize_whatsapp_id("23425456279692@lid") == "23425456279692"
    assert normalize_whatsapp_id("+923221540961") == "923221540961"
    assert normalize_whatsapp_id("923221540961:7@s.whatsapp.net") == "923221540961"
    assert normalize_whatsapp_id("") == ""


def test_expand_whatsapp_aliases():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        (session_dir / "lid-mapping-923221540961.json").write_text(json.dumps("12345"))
        (session_dir / "lid-mapping-12345_reverse.json").write_text(json.dumps("923221540961"))
        aliases = expand_whatsapp_aliases("12345", session_dir)
        assert "12345" in aliases
        assert "923221540961" in aliases


def test_allowlist_with_lid_resolution():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        (session_dir / "lid-mapping-923221540961.json").write_text(json.dumps("23425456279692"))
        (session_dir / "lid-mapping-23425456279692_reverse.json").write_text(json.dumps("923221540961"))
        al = Allowlist(["+923221540961"], session_dir=session_dir)
        assert al.is_allowed("23425456279692")
        assert al.is_allowed("923221540961")
        assert not al.is_allowed("99999999999")


def test_allowlist_wildcard():
    al = Allowlist(["*"])
    assert al.is_allowed("923221540961")
    assert al.is_allowed("anyone")