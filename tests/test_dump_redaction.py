"""cmd_dump must never write a configured secret to disk, even if a future edit
adds a field that reads one. This pins that guarantee at the redaction layer rather
than by enumerating every field cmd_dump currently writes."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent8088 import cli  # noqa: E402


def test_dump_redacts_a_configured_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.A, "APP_DIR", tmp_path)
    monkeypatch.setattr(cli.A, "APP_CONFIG", {"provider.openai.api_key": "sk-super-secret-value"})
    monkeypatch.setattr(cli.A, "collect_secret_values", lambda config: ["sk-super-secret-value"])

    cli.cmd_dump("")

    dump_text = (tmp_path / "dump.txt").read_text(encoding="utf-8")
    assert "sk-super-secret-value" not in dump_text
