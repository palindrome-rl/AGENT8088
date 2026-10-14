"""Where a smoke-test command starts, per backend."""
from pathlib import Path


def test_native_windows_reads_the_script_in_place_with_forward_slashes(engine, tmp_path):
    here = tmp_path / "artifacts" / "sub"
    here.mkdir(parents=True)
    prefix = engine._smoke_cd_prefix(here, Path("sub"), windows_native=True)
    assert prefix == f'cd /d "{here.resolve().as_posix()}" && '
    assert "\\" not in prefix, "a backslash path would be rewritten to the unreadable read-only copy"


def test_other_backends_start_in_the_copy_and_cd_to_the_subfolder(engine, tmp_path):
    assert engine._smoke_cd_prefix(tmp_path, Path("."), windows_native=False) == ""
    assert engine._smoke_cd_prefix(tmp_path / "a b", Path("a b"), windows_native=False) == "cd 'a b' && "
