"""A hex value its own label calls a digest is not a credential.

Seen in a benchmark run: a correct build wrote `out/manifest.json` with the SHA-256 of its output, the scan reported
it as a credential written in clear text, and the run ended NOT VERIFIED. A bare hex string, and a hex value behind a
label that names a key, token or password, are still reported."""
import pytest

from agent8088 import secret_scan as ss

# Fabricated digests of public test labels; never real credentials.
SHA256 = "5312592ad080ac0786ec7754316657e16531ec3dffce7e56d4445466e99d61a2"
SHA1 = "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3"
MD5 = "098f6bcd4621d373cade4e832627b4f6"


@pytest.mark.parametrize("text", [
    f'{{"modules": 120, "symbols": 121, "links": 355, "sha256": "{SHA256}"}}',
    f'"SHA-256": "{SHA256}"',
    f"checksum = {SHA256}",
    f"file_hash: {SHA1}",
    f"content-digest={SHA256}",
    f"etag: '{MD5}'",
    f"md5={MD5}",
    f"sha256_hex = '{SHA256}'",
    f"{SHA256}  out/app.bin",
    f"{SHA256} *out/app.bin",
])
def test_labelled_digests_are_not_credentials(text):
    assert ss.find_secrets(text) == []


@pytest.mark.parametrize("text", [
    SHA256,                                         # no label: could be anything
    f"value: {SHA256}",                             # a label that says nothing about hashing
    f"password_hash = {SHA256}",                    # a password's hash is still worth a look
    f"api_key_sha256 = {SHA256}",  # gitleaks:allow -- fabricated scanner fixture
    f"token_digest: {SHA256}",  # gitleaks:allow -- fabricated scanner fixture
    f"hmac_secret_hash={SHA256}",  # gitleaks:allow -- fabricated scanner fixture
    "sha256: 5312592ad080ac0786ec7754316657e16531ec3dffce7e56d444",   # not a digest length
    f"sha256: {SHA256} and api_key=9f86d081884c7d659a2feaa0c55ad015",  # gitleaks:allow -- fabricated scanner fixture
    f"{SHA256}",
])
def test_unlabelled_or_secret_labelled_hex_is_still_reported(text):
    assert ss.find_secrets(text)


def test_a_manifest_file_scans_clean(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{\n  "modules": 120,\n  "sha256": "' + SHA256 + '"\n}\n', encoding="utf-8")
    assert ss.scan_files([str(manifest)]) == []
