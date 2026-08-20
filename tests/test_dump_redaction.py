"""cmd_dump must never write a configured secret to disk, even if a future edit
adds a field that reads one. This pins that guarantee at the redaction layer rather
than by enumerating every field cmd_dump currently writes."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent8088 import cli  # noqa: E402


def test_dump_redacts_a_configured_secret(tmp_path, monkeypatch):
    # cmd_dump resolves its output path via A._agent_data_dir() (~/.agent8088,
    # honoring AGENT8088_HOME), not A.APP_DIR -- APP_DIR is the package source
    # dir, and writing there breaks `agent8088 --update`'s git-clean check for
    # an editable install. Point it at tmp_path instead of the real home dir.
    monkeypatch.setattr(cli.A, "_agent_data_dir", lambda: tmp_path)
    monkeypatch.setattr(cli.A, "APP_CONFIG", {"provider.openai.api_key": "sk-super-secret-value"})
    monkeypatch.setattr(cli.A, "collect_secret_values", lambda config: ["sk-super-secret-value"])
    # Make the secret actually land in cmd_dump's assembled `lines` (via the
    # "Model:" line) so the test would fail if the redaction loop were ever
    # removed. Without this, the secret never appears in `text` and the
    # assertion below passes vacuously regardless of whether redaction runs.
    monkeypatch.setattr(cli.A, "MODEL_NAME", "sk-super-secret-value")
    # Hermetic: cmd_dump also calls _endpoint_probe (real socket connect) and
    # A.sandbox_status() (reads real host state). Stub both so this test never
    # touches the network or the host's real config.
    monkeypatch.setattr(cli, "_endpoint_probe", lambda url: "stubbed")
    monkeypatch.setattr(cli.A, "sandbox_status", lambda: {
        "requested": "auto", "resolved": "unavailable",
        "detail": "test", "verification": "n/a",
    })

    cli.cmd_dump("")

    dump_text = (tmp_path / "dump.txt").read_text(encoding="utf-8")
    assert "sk-super-secret-value" not in dump_text
