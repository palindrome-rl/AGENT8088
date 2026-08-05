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

# --- Regression: from_config pooled every platform into one flat set ---
# is_allowed() took no platform, so an id listed under slack_allowed_users was
# also accepted on discord/whatsapp. Ids are now scoped to their platform.

def test_from_config_scopes_ids_to_their_platform():
    al = Allowlist.from_config({
        "slack_allowed_users": "U_SLACK",
        "discord_allowed_users": "123456789",
        "whatsapp_allowed_users": "+15551234567",
    })
    assert al.is_allowed("U_SLACK", platform="slack")
    assert not al.is_allowed("U_SLACK", platform="discord")
    assert not al.is_allowed("U_SLACK", platform="whatsapp")

    assert al.is_allowed("123456789", platform="discord")
    assert not al.is_allowed("123456789", platform="slack")

    assert al.is_allowed("+15551234567", platform="whatsapp")
    assert not al.is_allowed("+15551234567", platform="slack")


def test_platform_omitted_keeps_union_behaviour():
    """Callers that don't pass a platform keep the old permissive union so
    nothing silently breaks."""
    al = Allowlist.from_config({"slack_allowed_users": "U_SLACK"})
    assert al.is_allowed("U_SLACK")
    assert "U_SLACK" in al


def test_wildcard_still_allows_any_platform():
    al = Allowlist.from_config({"slack_allowed_users": "*"})
    assert al.is_allowed("anyone", platform="slack")


def test_unknown_platform_denied_when_scoped():
    al = Allowlist.from_config({"slack_allowed_users": "U_SLACK"})
    assert not al.is_allowed("U_SLACK", platform="signal")


def test_manually_added_ids_apply_to_all_platforms():
    """Allowlist(list) with no platform mapping (and .add()) stays global."""
    al = Allowlist(["U1"])
    al.add("U2")
    assert al.is_allowed("U1", platform="slack")
    assert al.is_allowed("U2", platform="discord")


def test_whatsapp_bare_number_matching_still_works_per_platform():
    al = Allowlist.from_config({"whatsapp_allowed_users": "+15551234567"})
    assert al.is_allowed("15551234567", platform="whatsapp")
    assert not al.is_allowed("15551234567", platform="slack")


def test_runner_passes_platform_to_allowlist():
    """The gate in GatewayRunner.on_message must scope by event.platform."""
    import inspect
    from agent8088.gateway import runner as R
    source = inspect.getsource(R.GatewayRunner.on_message)
    assert "event.platform" in source, "on_message does not scope the allowlist by platform"
