from types import SimpleNamespace

from agent8088 import cli


class _Provider:
    name = "searxng"

    def setup_schema(self):
        return {"badge": "local", "tag": "configured"}

    def is_available(self, _ctx):
        return True


class _Registry:
    def all(self):
        return [_Provider()]


def test_search_status_probes_configured_searxng(monkeypatch):
    monkeypatch.setattr(cli.A, "WEB_SEARCH_REGISTRY", _Registry())
    monkeypatch.setattr(cli.A, "_search_context", lambda: SimpleNamespace())
    calls = []
    monkeypatch.setattr(
        cli.A.web_search,
        "probe_searxng",
        lambda ctx: calls.append(ctx) or False,
    )

    rows = cli._search_provider_rows()

    assert rows == [("searxng", "local", False, "configured")]
    assert len(calls) == 1


def test_search_status_reports_healthy_searxng_ready(monkeypatch):
    monkeypatch.setattr(cli.A, "WEB_SEARCH_REGISTRY", _Registry())
    monkeypatch.setattr(cli.A, "_search_context", lambda: SimpleNamespace())
    monkeypatch.setattr(cli.A.web_search, "probe_searxng", lambda _ctx: True)

    assert cli._search_provider_rows()[0][2] is True
