"""Credential-shaped detection in pasted text and in files a run wrote."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent8088 import secret_scan  # noqa: E402


# Synthetic SHA-256 of a public test label; never a real credential.
PASTED_HASH = "2e8846ed3978f8ba0be4a804f903ccd4b353010a099270bed0343954f9311a2b"


@pytest.mark.parametrize("text,kind", [
    (PASTED_HASH, "opaque"),
    ("here you go sk-abcdefghijklmnopqrstuvwxyz0123456789ABCD ok", "API key (sk-)"),
    ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789", "GitHub PAT"),
    ("key AKIAIOSFODNN7EXAMPLE", "AWS access key id"),
    ("xoxb-123456789012-1234567890123-abcdefghijklmnopqrstuvwx", "Slack token"),
    ("AIzaSyA1234567890abcdefghijklmnopqrstuvw", "Google API key"),
    ("postgres://admin:Sup3rSecretPassw0rd@db.internal:5432/app", "connection string"),
    ("-----BEGIN RSA PRIVATE KEY-----", "Private key"),
])
def test_flags_credentials(text, kind):
    findings = secret_scan.find_secrets(text)
    assert findings, "expected a finding for %r" % text
    assert any(kind in f.kind for f in findings), [f.kind for f in findings]


@pytest.mark.parametrize("text", [
    "hello world",
    "The limit is CPU",
    "Please refactor the database connection string handling.",
    "https://github.com/palindrome-rl/AGENT8088/blob/main/README.md",
    "src/agent8088/engine.py",
    "contact imroze.aslam@example.com for access",
    "origin/development",
    "max_tokens is a config option",
    "OPENAI_API_KEY",
    "/home/user/.ssh/id_rsa",
    "Version 1.2.3 released",
    "2026-10-14T09:32:11Z",
    "1234567890123456789012345",
    "class SimpleHandler(BaseHTTPRequestHandler):",
    "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer",
    "AbstractBaseClassFactory",
])
def test_ignores_ordinary_text(text):
    assert secret_scan.find_secrets(text) == [], text


def test_mask_never_reveals_a_short_secret():
    # Under twelve characters nothing is partially shown.
    assert secret_scan.mask("short") == "*****"
    long_mask = secret_scan.mask(PASTED_HASH)
    assert long_mask == "2e88...1a2b"
    assert PASTED_HASH not in long_mask


def test_warning_names_shape_without_quoting_the_secret():
    text = secret_scan.warning(secret_scan.find_secrets(PASTED_HASH))
    assert PASTED_HASH not in text
    assert "2e88...1a2b" in text


def test_scan_files_reports_path_and_line(tmp_path):
    target = tmp_path / "config.py"
    target.write_text(
        "API_KEY = ''\n"
        "TOKEN = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'\n",
        encoding="utf-8")
    hits = secret_scan.scan_files([target])
    assert len(hits) == 1
    path, line, finding = hits[0]
    assert Path(path).name == "config.py"
    assert line == 2
    assert "GitHub" in finding.kind


def test_scan_files_skips_binary_and_missing(tmp_path):
    binary = tmp_path / "logo.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
    assert secret_scan.scan_files([binary, tmp_path / "gone.py"]) == []


def test_scan_files_does_not_raise_on_unreadable(tmp_path):
    # A file the run wrote but that is now gone must not take the run down.
    assert secret_scan.scan_files([tmp_path / "deleted.py"]) == []


def test_opaque_gate_rejects_digits_only():
    # Timestamps and counters are long and opaque but never credentials.
    assert not secret_scan.looks_like_opaque_credential("1234567890123456789012345")
    assert secret_scan.looks_like_opaque_credential(PASTED_HASH)


# ---------------------------------------------------------------- engine wiring
def _state():
    from agent8088.trajectory import TrajectoryState
    return TrajectoryState({})


def test_secrets_step_flags_a_file_holding_a_credential(tmp_path):
    from agent8088 import engine
    target = tmp_path / "config.py"
    target.write_text('API_KEY = "%s"\n' % PASTED_HASH, encoding="utf-8")
    state = _state()
    nudge = engine._secrets_step(state, [str(target)])
    assert nudge is not None, "a credential in a written file must be reported"
    assert "clear text" in nudge.unmet
    assert PASTED_HASH not in nudge.unmet  # the note names the file, not the value


def test_a_credential_the_model_ignored_is_still_reported(tmp_path):
    """The suppression bug: retiring a file that is still dirty hid the leak.

    First pass flags it, the model does nothing, second pass must flag it again
    and the end-of-run note must name it. Retiring on the hit made the second
    pass see an empty pending list and the run finish as a plain success.
    """
    from agent8088 import engine
    target = tmp_path / "config.py"
    target.write_text('API_KEY = "%s"\n' % PASTED_HASH, encoding="utf-8")
    state = _state()

    first = engine._secrets_step(state, [str(target)])
    assert first is not None
    second = engine._secrets_step(state, [str(target)])
    assert second is not None, "an unfixed credential stopped being reported"


def test_a_clean_file_is_retired_and_not_rescanned(tmp_path):
    from agent8088 import engine
    target = tmp_path / "ok.py"
    target.write_text('API_KEY = os.getenv("API_KEY")\n', encoding="utf-8")
    state = _state()
    assert engine._secrets_step(state, [str(target)]) is None
    assert state.secrets_unchecked([str(target)]) == []


def test_fixed_file_stops_being_reported(tmp_path):
    from agent8088 import engine
    target = tmp_path / "config.py"
    target.write_text('API_KEY = "%s"\n' % PASTED_HASH, encoding="utf-8")
    state = _state()
    assert engine._secrets_step(state, [str(target)]) is not None
    target.write_text('API_KEY = os.getenv("API_KEY")\n', encoding="utf-8")
    state.data["mutation_count"] = 1  # a rewrite rescan
    assert engine._secrets_step(state, [str(target)]) is None

# ------------------------------------------------- identifiers and sizes are not keys
@pytest.mark.parametrize("text", [
    "raiseSpA2Speed(target, 1)",
    "const raiseSpA2Speed = (m) => m.spa * 2;",
    "max_tokens_v2 = 4096",
    "applyStatusEffectLevel2()",
    "OLLAMA_MODEL=tev1:0.8b",
    "model = qwen3:0.6b",
    "MODEL_NAME=llama3.2:70b",
    "teamBuilderRosterSlot3Picker",
])
def test_code_identifiers_and_model_sizes_are_not_credentials(text):
    assert secret_scan.find_secrets(text) == []


@pytest.mark.parametrize("value", [
    "aB3dE9gH2jK5mN8pQ1rS4tU7vW0xYz",     # random mixed-case with digits
    "q8Zr1Lw0Tx7Vb3Nc5Mk2Hd9Jf4Gs6Ya",
    "AbCdEfGhIjKlMnOpQrStUvWx12",
    "9f8e7d6c5b4a39281706f5e4d3c2b1a0",   # hex, few letters
    "badc0ffeeface12facade00dead",        # hex that spells words and has only two digits
])
def test_random_looking_keys_are_still_found(value):
    found = secret_scan.find_secrets(f'token = "{value}"')
    assert found, value


# ------------------------------------------------- strict camelCase is code
@pytest.mark.parametrize("text", [
    "effects: ['selfSpAtkUp2', 'selfSpAtkUp1SpDefUp1']",
    "const k = 'selfAtkUp1SpAtkUp1SpdUp1DefDown1SpDefDown1';",
    "applyBurnDamageEndOfTurn(target, 2)",
    "getActiveIdx = Engine.getActiveIdx",
    "TeamBuilderRosterSlotPicker",
    "pokemonHeldItemChoiceBand2",
])
def test_strict_camelcase_words_are_not_credentials(text):
    assert secret_scan.find_secrets(text) == []


@pytest.mark.parametrize("value", [
    "Bc1De2Fg3Hi4Jk5Lm6No7Pq8Rs9Tu",       # alternating upper/lower pairs with digits between them
    "xK9mQ2vL8nR4tY7wZ3bC6dF1hJ5",         # base62 with digits through it
    "aB3dE9gH2jK5mN8pQ1rS4tU7vW0",
])
def test_random_mixed_case_is_still_found_after_the_camelcase_rule(value):
    assert secret_scan.find_secrets(f'api_token = "{value}"'), value
