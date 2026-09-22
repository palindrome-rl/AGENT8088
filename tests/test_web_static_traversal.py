"""The SPA fallback must serve the built frontend, and nothing else.

`_mount_static` registers a catch-all GET whose path segment was joined
straight onto dist/. uvicorn percent-decodes the request path before routing,
so `GET /%2e%2e/%2e%2e/etc/hosts` arrived as `../../etc/hosts` and the
handler happily returned it: an unauthenticated read of any file the user can
read, including ~/.agent8088/config.txt and its provider keys. Verified live
against a real uvicorn instance before the fix.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def served(tmp_path):
    """A built-frontend layout with a secret sitting just outside it."""
    from agent8088 import web_server

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("SPA")
    (dist / "favicon.png").write_text("icon")
    (tmp_path / "secret.txt").write_text("TOP-SECRET-HOST-FILE")

    app = FastAPI()
    web_server._mount_static(app, dist)
    return TestClient(app), tmp_path


@pytest.mark.parametrize("path", [
    "/%2e%2e/secret.txt",
    "/..%2fsecret.txt",
    "/%2e%2e%2fsecret.txt",
    "/" + "%2e%2e/" * 12 + "etc/hosts",
    "/assets/../../secret.txt",
])
def test_traversal_never_escapes_the_dist_directory(served, path):
    client, _ = served
    response = client.get(path)
    assert "TOP-SECRET-HOST-FILE" not in response.text
    assert "# Host Database" not in response.text


def test_real_frontend_files_are_still_served(served):
    client, _ = served
    assert client.get("/favicon.png").text == "icon"


def test_unknown_route_still_falls_back_to_the_spa(served):
    """The whole point of the catch-all: deep links must reach index.html."""
    client, _ = served
    assert client.get("/sessions/some-name").text == "SPA"


def test_unmatched_api_path_is_still_a_404(served):
    client, _ = served
    assert client.get("/api/nope").status_code == 404
