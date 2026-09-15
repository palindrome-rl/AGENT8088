"""The Web UI is where someone clicks 'apply' against a file that has moved on."""
from fastapi.testclient import TestClient

from agent8088 import web_server as W


def test_history_lists_metadata_without_the_findings(monkeypatch):
    """A listing that carries every finding also carries every quoted line."""
    monkeypatch.setattr(W._eng(), "review_history",
                        lambda limit=50: [{"id": "rev-1", "created": 0.0, "repo": "/r",
                                           "mode": "native", "engine_version": "1.12.1",
                                           "status": "complete", "findings": 3}])
    body = TestClient(W.app).get("/api/reviews").json()

    assert body["reviews"][0]["findings"] == 3
    assert "payload" not in body["reviews"][0]
    assert "existing_code" not in str(body)


def test_one_review_is_re_checked_not_replayed(monkeypatch):
    """The stored verdict was true when the review ran; this endpoint must say now."""
    seen = {}

    def fake_reopen(review_id, root=None):
        seen["id"] = review_id
        return {"findings": [{"path": "a.py", "start_line": 1, "position_valid": False,
                              "verification": "stale"}], "reopened": True}

    monkeypatch.setattr(W._eng(), "reopen_review", fake_reopen)
    body = TestClient(W.app).get("/api/reviews/rev-abc").json()

    assert seen["id"] == "rev-abc"
    assert body["reopened"] is True
    assert body["findings"][0]["verification"] == "stale"


def test_unknown_review_is_404(monkeypatch):
    monkeypatch.setattr(W._eng(), "reopen_review", lambda review_id, root=None: None)
    assert TestClient(W.app).get("/api/reviews/rev-nope").status_code == 404


def test_review_result_is_readable_and_bounded():
    import json
    result = W._tool_result_payload("review_code", json.dumps({
        "engine": "open-code-review", "status": "complete", "review_id": "rev-test",
        "coverage": {"partial": True}, "findings": [
            {"path": "a.py", "start_line": 3, "severity": "high", "message": "wrong percentage"}
        ]}))
    assert "a.py:3" in result["result"]
    assert "rev-test" in result["result"]
    assert "partial" in result["result"]
    assert "{\"engine\"" not in result["result"]
