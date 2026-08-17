"""Hybrid retrieval: what each leg contributes and how RRF resolves them.

Vectors are supplied by the test rather than a model, so every ranking assertion
here is about the fusion logic and nothing else. No network, no model, no clock.
"""
import pytest

from agent8088.memory.store import DEFAULT_RRF_K, MemoryStore


def rrf(*ranks, k=DEFAULT_RRF_K):
    return sum(1.0 / (k + rank) for rank in ranks)


def add(store, text, vector, *, user_id="owner"):
    return store.add(text, user_id=user_id, embedding=vector, embed_model="m")


def test_a_memory_found_by_both_legs_beats_one_found_by_either(store):
    # "uv" is in the text of both, so BM25 ranks both; only the first is close to
    # the query vector, so it alone wins the vector leg -- and the fusion.
    both = add(store, "user prefers uv for python", [1.0, 0.0])
    words_only = add(store, "uv was mentioned once in passing", [0.0, 1.0])
    results = store.search("uv", user_id="owner", embedding=[1.0, 0.0], model="m")
    assert [row["id"] for row in results][0] == both
    assert words_only in [row["id"] for row in results]


def test_a_memory_only_the_vector_leg_finds_still_places(store):
    """The whole point of the vector leg: no shared words with the query."""
    add(store, "the team standardised on uv", [1.0, 0.0])
    results = store.search("which package manager", user_id="owner",
                           embedding=[1.0, 0.0], model="m")
    assert [row["text"] for row in results] == ["the team standardised on uv"]
    assert results[0]["bm25_rank"] is None
    assert results[0]["vector_rank"] == 1


def test_a_memory_only_the_keyword_leg_finds_still_places(store):
    """And the point of the keyword leg: an exact token an embedder blurs."""
    add(store, "the flag is --plan-audit-revert", [0.0, 1.0])
    results = store.search("--plan-audit-revert", user_id="owner",
                           embedding=[1.0, 0.0], model="m")
    assert [row["text"] for row in results] == ["the flag is --plan-audit-revert"]
    assert results[0]["bm25_rank"] == 1
    assert results[0]["vector_rank"] is None


def test_scores_are_the_sum_of_reciprocal_ranks(store):
    add(store, "uv is the package manager", [1.0, 0.0])
    results = store.search("uv", user_id="owner", embedding=[1.0, 0.0], model="m")
    assert results[0]["score"] == pytest.approx(rrf(1, 1))


def test_agreement_on_mediocre_ranks_beats_a_single_first_place(store):
    """Why RRF rather than max(): two legs agreeing at rank 2 outweighs one leg's
    rank 1, which is the behaviour wanted when neither leg is trustworthy."""
    assert rrf(2, 2) > rrf(1)


def test_the_damping_constant_keeps_first_and_second_close(store):
    """k stops rank 1 from crushing rank 2. Without it (k=0) the gap is 2x."""
    assert rrf(1) / rrf(2) < 1.05
    assert rrf(1, k=0) / rrf(2, k=0) == pytest.approx(2.0)


def test_the_limit_is_applied_after_fusion_not_per_leg(store):
    for index in range(6):
        add(store, f"uv fact number {index}", [1.0, index / 10.0])
    results = store.search("uv", user_id="owner", embedding=[1.0, 0.0], model="m",
                           limit=3)
    assert len(results) == 3


def test_a_score_floor_drops_weak_hits(store):
    add(store, "uv is the package manager", [1.0, 0.0])
    assert store.search("uv", user_id="owner", embedding=[1.0, 0.0], model="m",
                        min_score=1.0) == []


def test_recall_works_with_no_embedder_at_all(store):
    """The degraded path: embedder missing, BM25 carries the whole ranking."""
    add(store, "the repo has no CI", [1.0, 0.0])
    results = store.search("CI", user_id="owner", embedding=[], model="m")
    assert [row["text"] for row in results] == ["the repo has no CI"]
    assert results[0]["vector_rank"] is None


def test_recall_works_when_only_the_vector_leg_can_answer(store):
    add(store, "the repo has no CI", [1.0, 0.0])
    # Punctuation-only query: nothing survives for FTS5, so BM25 contributes
    # nothing and the vector leg must still return the memory.
    results = store.search("?!", user_id="owner", embedding=[1.0, 0.0], model="m")
    assert [row["text"] for row in results] == ["the repo has no CI"]


def test_no_match_returns_nothing_rather_than_everything(store):
    add(store, "the repo has no CI", [1.0, 0.0])
    assert store.search("zzzz", user_id="owner", embedding=[], model="m") == []


def test_an_empty_store_returns_nothing(store):
    assert store.search("anything", user_id="owner", embedding=[1.0], model="m") == []


def test_read_count_cannot_outrank_relevance(store):
    """Access count is recorded but never scored. An earlier version multiplied by
    it and let a frequently-read irrelevant memory win."""
    relevant = add(store, "uv is the package manager", [1.0, 0.0])
    popular = add(store, "uv was mentioned", [0.0, 1.0])
    store.connect().execute("UPDATE memories SET access_count=100 WHERE id=?", (popular,))
    store.connect().commit()
    results = store.search("uv", user_id="owner", embedding=[1.0, 0.0], model="m")
    assert results[0]["id"] == relevant


def test_an_exact_tie_is_broken_toward_the_newer_fact(store):
    """The realistic exact tie: the legs disagree symmetrically, so RRF lands on
    the same score for both. One is first by words and second by meaning, the
    other the reverse. The later fact wins, because it is the one that supersedes.
    """
    add(store, "deploys go to the staging cluster", [0.6, 0.8])
    newer = add(store, "deploys go to the production cluster", [1.0, 0.0])
    results = store.search("deploys", user_id="owner", embedding=[1.0, 0.0], model="m")
    assert {row["bm25_rank"] for row in results} == {1, 2}
    assert {row["vector_rank"] for row in results} == {1, 2}
    assert results[0]["score"] == results[1]["score"]
    assert results[0]["id"] == newer


def test_a_memory_with_no_similarity_is_not_credited_by_the_vector_leg(store):
    """Zero similarity is no signal, not the best of a bad set. Without this the
    vector leg hands rank 1 to an unrelated memory whenever the store is small."""
    add(store, "unrelated fact about postgres", [0.0, 1.0])
    assert store._vector_leg([1.0, 0.0], user_id="owner", model="m") == []


def test_deeper_leg_depth_than_limit_lets_a_low_word_rank_win(store):
    """A memory ranked poorly by words and first by meaning must still be able to
    win, which it cannot if a leg only reports as many rows as the final limit."""
    for index in range(10):
        add(store, f"uv trivia {index}", [0.0, 1.0])
    target = add(store, "uv is the package manager", [1.0, 0.0])
    results = store.search("uv", user_id="owner", embedding=[1.0, 0.0], model="m",
                           limit=1)
    assert results[0]["id"] == target
