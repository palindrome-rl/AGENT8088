"""Tier 0: every saved benchmark run in tests/verdict_corpus/ is judged again by this branch's verdict code.

A case is a real run, scrubbed of local names: its reply, its commands and their output, the files it left and its
page/script check output. Its label says the verdict a correct harness gives on that evidence, and who a failure
belongs to (the model, the environment, the harness or the grader). See bench/corpus.py for what is replayed.

- An ordinary case must get its labelled verdict. A change that flips one must change the label in the same PR, so
  the flip is visible in review.
- A `known_gap` case is one the current code is known to get wrong. It must still get it wrong: a fix that makes it
  pass must update the label (and say so in the PR).
- A `replay_gap` case cannot be judged offline the way the run judged it, and is skipped. It stays for the day the
  replay can.

The runs were made on Windows and their paths are Windows paths, so the cases run there (CI has a Windows job for
them); elsewhere they are skipped.

Add a run:  python bench/corpus.py export <run folder> --id <task>--<batch>--<trial>, then add its label.
"""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))

import corpus

LABELS = corpus.labels()
FIELDS = {"expected", "work", "note", "known_gap", "replay_gap"}
WORK = {"none", "model", "environment", "harness", "grader", "infra"}

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the saved runs are Windows runs")


def test_every_case_has_a_label_and_every_label_a_case():
    cases = {p.name[:-len(".json.gz")] for p in corpus.CORPUS.glob("*.json.gz")}
    assert cases == set(LABELS)


@pytest.mark.parametrize("case_id", sorted(LABELS))
def test_labels_are_well_formed(case_id):
    label = LABELS[case_id]
    assert set(label) <= FIELDS
    assert label["expected"] in ("done", "not_verified")
    assert label["work"] in WORK
    # A verdict that is not the grader's needs a reason a reviewer can check.
    grader = corpus.load(case_id)["grader"]["passed"]
    if (label["expected"] == "done") != bool(grader) or label.get("known_gap") or label.get("replay_gap"):
        assert label["note"], case_id


def test_the_cases_carry_no_local_names():
    import gzip
    import re
    # A local network address, or a user folder whose name was not made neutral (x's and 0's).
    names = re.compile(r"\b192\.168\.\d+\.\d+|[A-Za-z]:(?:\\|/)+Users(?:\\|/)+(?![xX0.~-]+\b|user\b|public\b|"
                       r"default\b)[\w.~-]+", re.IGNORECASE)
    for path in corpus.CORPUS.glob("*.json.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            assert not names.search(handle.read()), path.name


@windows_only
@pytest.mark.parametrize("case_id", sorted(LABELS))
def test_the_verdict_on_a_saved_run(case_id, engine):
    label = LABELS[case_id]
    if label.get("replay_gap"):
        pytest.skip("replay gap: " + label["note"])
    # Not tmp_path: its folder names hold "pytest" and "test_", and a path with "test" in it makes a failed command
    # look like a test run to the verdict code.
    work = Path(tempfile.mkdtemp(prefix="replay-"))
    try:
        got = corpus.replay(corpus.load(case_id), work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    why = f"{case_id}: got {got['verdict']} ({'; '.join(got['reasons']) or 'no reasons'}); label: {label['note']}"
    if label.get("known_gap"):
        assert got["verdict"] != label["expected"], (
            "a known gap now passes: update its label (drop known_gap) in this change. " + why)
    else:
        assert got["verdict"] == label["expected"], why
