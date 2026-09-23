"""A review has to outlive the turn that produced it, without outliving its truth."""

from agent8088 import review_store


def _result(findings=None):
    return {"schema_version": 1, "status": "complete", "mode": "native",
            "engine": "open-code-review", "engine_version": "1.12.1",
            "findings": findings if findings is not None else [
                {"id": "billing.py:16:0", "path": "billing.py", "start_line": 16,
                 "end_line": 16, "severity": "high", "category": "bug",
                 "message": "Floor division truncates refunds.",
                 "existing_code": "return (amount * percent) // 100",
                 "suggested_code": "return round(amount * percent / 100, 2)",
                 "position_valid": True, "verification": "unverified"}],
            "warnings": []}


def test_a_saved_review_reads_back_whole(tmp_path):
    store = tmp_path / "reviews.db"
    review_id = review_store.save(store, tmp_path, _result())

    assert review_id.startswith("rev-")
    loaded = review_store.load(store, review_id)
    assert loaded["findings"][0]["message"] == "Floor division truncates refunds."
    assert loaded["engine_version"] == "1.12.1"


def test_unknown_and_malformed_ids_return_nothing(tmp_path):
    store = tmp_path / "reviews.db"
    review_store.save(store, tmp_path, _result())

    assert review_store.load(store, "rev-doesnotexist") is None
    assert review_store.load(store, "../../etc/passwd") is None
    assert review_store.load(store, "") is None


def test_recent_lists_newest_first_without_the_payload(tmp_path):
    """History is a menu, so it must not drag every finding into the listing."""
    store = tmp_path / "reviews.db"
    first = review_store.save(store, tmp_path, _result())
    second = review_store.save(store, tmp_path, _result(findings=[]))

    rows = review_store.recent(store)

    assert [row["id"] for row in rows][:2] == [second, first]
    assert rows[0]["findings"] == 0 and rows[1]["findings"] == 1
    assert "payload" not in rows[0]


def test_reopening_replaces_the_stored_verdict_rather_than_trusting_it(tmp_path):
    """position_valid was true when the review ran; that is not a claim about now.

    Acting on the stored answer is the stale-diff failure the design note asks
    to prevent, so reopen recomputes it. A finding that no longer matches is
    marked stale rather than dropped -- the reviewer still said something about
    that file, and losing it silently is worse than saying it moved.
    """
    store = tmp_path / "reviews.db"
    review_id = review_store.save(store, tmp_path, _result())

    reopened = review_store.reopen(store, review_id, tmp_path,
                                   recheck=lambda root, finding: False)

    assert reopened["findings"][0]["position_valid"] is False
    assert reopened["findings"][0]["verification"] == "stale"
    assert reopened["reopened"] is True
    assert len(reopened["findings"]) == 1, "a stale finding is marked, never dropped"


def test_history_is_bounded(tmp_path):
    """A working history, not an audit log: old reviews fall off the end."""
    store = tmp_path / "reviews.db"
    kept = []
    for _ in range(review_store.MAX_REVIEWS + 5):
        kept.append(review_store.save(store, tmp_path, _result(findings=[])))

    rows = review_store.recent(store, limit=200)

    assert len(rows) <= review_store.MAX_REVIEWS
    assert review_store.load(store, kept[-1]) is not None, "the newest must survive"
    assert review_store.load(store, kept[0]) is None, "the oldest must have been pruned"


def test_a_broken_store_never_breaks_the_review(tmp_path):
    """Persistence is a convenience; failing to persist must not lose the result."""
    blocked = tmp_path / "reviews.db"
    blocked.mkdir()  # a directory where the database should be

    assert review_store.save(blocked, tmp_path, _result()) == ""
    assert review_store.load(blocked, "rev-abc") is None
    assert review_store.recent(blocked) == []


def test_connections_release_the_database_file(tmp_path):
    store = tmp_path / "reviews.db"
    review_id = review_store.save(store, tmp_path, _result())
    review_store.load(store, review_id)
    review_store.recent(store)
    store.unlink()  # Windows refuses this if any sqlite connection remains open.
    assert not store.exists()


def test_a_relocated_finding_is_moved_and_says_so(tmp_path):
    """A recheck may answer with the code's current lines. The finding then
    points there -- a fix must land where the code is -- and records where
    the review originally put it."""
    store = tmp_path / "reviews.db"
    review_id = review_store.save(store, tmp_path, _result())
    original = review_store.load(store, review_id)["findings"][0]

    reopened = review_store.reopen(store, review_id, tmp_path,
                                   recheck=lambda root, finding: (41, 42))
    moved = reopened["findings"][0]

    assert moved["position_valid"] is True
    assert (moved["start_line"], moved["end_line"]) == (41, 42)
    assert moved["moved_from"] == original["start_line"]
    assert moved["verification"] == "moved"


def test_a_finding_still_at_its_line_is_not_marked_moved(tmp_path):
    store = tmp_path / "reviews.db"
    review_id = review_store.save(store, tmp_path, _result())
    original = review_store.load(store, review_id)["findings"][0]
    here = (original["start_line"], original.get("end_line") or original["start_line"])

    reopened = review_store.reopen(store, review_id, tmp_path,
                                   recheck=lambda root, finding: here)

    assert reopened["findings"][0]["position_valid"] is True
    assert "moved_from" not in reopened["findings"][0]
