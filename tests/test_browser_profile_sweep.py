"""browse_page cleans up its own Chromium profile on a normal return and on a
timeout, but a hard kill of agent8088 (SIGKILL, a crash, Ctrl+C at the wrong
moment) never runs that `finally` - so the profile directory stays on disk and
the Chromium it launched is reparented to init and runs forever. Observed on a
real machine: an orphan alive for ~2 days plus 12 abandoned profile
directories, with nothing in the product that would ever reap them.

Every process interaction here goes through injected seams: these tests must
never enumerate or signal a real process on the machine running them.
"""
import os
import time

import pytest

from agent8088 import engine as A

PREFIX = "agent8088-browser-profile-"


def _profile_dir(root, name, age_seconds):
    path = root / f"{PREFIX}{name}"
    path.mkdir()
    (path / "Default").mkdir()
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def test_a_stale_profile_directory_is_removed(tmp_path):
    stale = _profile_dir(tmp_path, "old", age_seconds=7 * 3600)

    A._sweep_stale_browser_profiles(
        root=tmp_path, list_processes=lambda: [], terminate=lambda pid: None)

    assert not stale.exists()


def test_a_profile_directory_from_a_live_session_is_left_alone(tmp_path):
    """A concurrent browse_page call owns a fresh directory. Deleting it would
    break a session that is working correctly - far worse than the leak."""
    fresh = _profile_dir(tmp_path, "live", age_seconds=30)

    A._sweep_stale_browser_profiles(
        root=tmp_path, list_processes=lambda: [], terminate=lambda pid: None)

    assert fresh.exists()


def test_unrelated_temp_directories_are_never_touched(tmp_path):
    innocent = tmp_path / "important-user-data"
    innocent.mkdir()
    stamp = time.time() - 30 * 86400
    os.utime(innocent, (stamp, stamp))

    A._sweep_stale_browser_profiles(
        root=tmp_path, list_processes=lambda: [], terminate=lambda pid: None)

    assert innocent.exists()


def test_the_orphan_holding_a_stale_profile_is_terminated(tmp_path):
    stale = _profile_dir(tmp_path, "abandoned", age_seconds=7 * 3600)
    killed = []

    A._sweep_stale_browser_profiles(
        root=tmp_path,
        list_processes=lambda: [
            (4242, 1, f"/path/to/Chromium --user-data-dir={stale} --headless"),
        ],
        terminate=killed.append,
    )

    assert killed == [4242]
    assert not stale.exists()


def test_a_process_holding_a_FRESH_profile_is_never_signalled(tmp_path):
    """The exact failure mode to avoid: killing the browser of a browsing
    session that is still running."""
    fresh = _profile_dir(tmp_path, "live", age_seconds=30)
    killed = []

    A._sweep_stale_browser_profiles(
        root=tmp_path,
        list_processes=lambda: [
            (4242, 999, f"/path/to/Chromium --user-data-dir={fresh} --headless"),
        ],
        terminate=killed.append,
    )

    assert killed == []
    assert fresh.exists()


def test_an_unrelated_process_is_never_signalled(tmp_path):
    _profile_dir(tmp_path, "abandoned", age_seconds=7 * 3600)
    killed = []

    A._sweep_stale_browser_profiles(
        root=tmp_path,
        list_processes=lambda: [
            (999, 1, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            (1000, 1, "/usr/bin/chromium --user-data-dir=/home/me/.config/chromium"),
        ],
        terminate=killed.append,
    )

    assert killed == []


def test_a_sweep_failure_never_breaks_the_browsing_call(tmp_path):
    """Cleanup is a courtesy. It must not be able to fail a real task."""
    _profile_dir(tmp_path, "old", age_seconds=7 * 3600)

    def exploding_lister():
        raise OSError("ps is unavailable in this sandbox")

    A._sweep_stale_browser_profiles(
        root=tmp_path, list_processes=exploding_lister, terminate=lambda pid: None)


def test_the_sweep_runs_before_a_browsing_session_starts(monkeypatch, tmp_path):
    """Wiring check: the reaper is useless if nothing ever calls it."""
    calls = []
    monkeypatch.setattr(A, "_sweep_stale_browser_profiles",
                        lambda *a, **k: calls.append(True))

    fake = pytest.importorskip("browser_use")  # noqa: F841
    import asyncio

    class _History:
        def final_result(self):
            return "done"

        def is_done(self):
            return True

    class _Agent:
        def __init__(self, *a, **k):
            pass

        async def run(self, max_steps=None):
            return _History()

        async def close(self):
            pass

    monkeypatch.setattr("browser_use.Agent", _Agent)
    monkeypatch.setattr(A, "_active_budget", None)
    asyncio.run(A._run_browser_agent("https://example.com", "read it", None))

    assert calls, "_run_browser_agent did not sweep stale profiles"


def test_a_live_orphan_is_reaped_even_though_its_profile_looks_fresh(tmp_path):
    """The case the first cut of this sweep got wrong.

    An orphaned browser keeps writing to its profile, so the directory's
    mtime is refreshed continuously and never ages past the threshold - the
    reaper would have skipped forever exactly the process it exists to kill.
    Reproduced on a real machine: nine live processes holding a profile whose
    mtime was minutes old, while their agent8088 had been dead for hours.

    Orphanhood (ppid == 1) is the honest signal: this code always outlives
    the browser it launched, so a parentless one is abandoned by definition.
    """
    live = _profile_dir(tmp_path, "orphaned-but-busy", age_seconds=5)
    killed = []

    A._sweep_stale_browser_profiles(
        root=tmp_path,
        list_processes=lambda: [
            (52933, 1, f"/ms-playwright/chromium/Chromium --user-data-dir={live}"),
        ],
        terminate=killed.append,
    )

    assert killed == [52933]
    assert not live.exists(), "the reaped orphan's profile should go with it"


def test_a_stale_directory_still_held_by_a_LIVE_session_is_kept(tmp_path):
    """Age alone must not authorise deletion: a long-running session's
    profile can age past the threshold while still in use."""
    held = _profile_dir(tmp_path, "long-running", age_seconds=9 * 3600)
    killed = []

    A._sweep_stale_browser_profiles(
        root=tmp_path,
        list_processes=lambda: [
            (777, 555, f"/ms-playwright/chromium/Chromium --user-data-dir={held}"),
        ],
        terminate=killed.append,
    )

    assert killed == []
    assert held.exists()
