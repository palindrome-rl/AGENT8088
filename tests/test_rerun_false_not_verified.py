"""Two false NOT VERIFIED results from a targeted benchmark rerun.

1. A slow build run briefly wrote `out` as a file, then let the build make `out/` the folder that holds app.bin and
   manifest.json. The written path `out` was then a folder full of files, but the missing-file check counts only
   non-empty files, so the run was told "out" was missing, answered that it could not make `out` a file, and was
   reported NOT VERIFIED although the grader passed it.
2. A run mapping where it could write sent `cd && dir /b ..\\data.csv 2>nul && (echo test > ..\\probe.txt 2>nul && ...)`.
   The step in brackets was read as the program "(echo", which is not a file command, so the word "test" made the
   whole probe "the latest check", and the run ended "the latest check failed" with every deliverable in place.
"""
import pytest

from agent8088 import completion as c
from agent8088.trajectory import _is_check_run, _segment_program


def test_a_written_path_that_is_now_a_folder_of_files_is_not_missing(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "app.bin").write_bytes(b"\x01\x02")
    reply = "The build wrote out/app.bin and out/manifest.json."
    assert c.missing_outputs("Fix the build.", reply, [str(out), str(out / "app.bin")], [tmp_path]) == []


def test_a_written_path_that_is_now_an_empty_folder_is_still_missing(tmp_path):
    (tmp_path / "out").mkdir()
    missing = c.missing_outputs("Fix the build.", "The build wrote out.", [str(tmp_path / "out")], [tmp_path])
    assert missing == [str(tmp_path / "out")]


PROBE = ("cd && echo --- && dir /b data.csv 2>nul && echo ---PARENT--- && dir /b ..\\data.csv 2>nul && "
         "(echo test > ..\\probe.txt 2>nul && echo WROTE || echo READONLY) && dir /b ..\\probe.txt 2>nul")


@pytest.mark.parametrize("segment, program", [
    ("(echo test > ..\\probe.txt 2>nul", "echo"),
    ("( dir /b out", "dir"),
    ("((type notes.txt", "type"),
    ("(python -m pytest -q", "python"),
])
def test_a_bracket_before_the_program_is_not_part_of_its_name(segment, program):
    assert _segment_program(segment) == program


def test_a_probe_with_a_bracketed_echo_is_not_a_check():
    assert not _is_check_run(PROBE)


def test_a_bracketed_test_run_is_still_a_check():
    assert _is_check_run("cd app && (python -m pytest -q || echo FAILED)")


SETUP = ("if (Test-Path .venv) { Remove-Item -Recurse -Force .venv }; python -m venv --without-pip .venv; "
         r'if (Test-Path .venv\Scripts\python.exe) { Write-Output "venv made" } else { Write-Output "no venv" }')


def test_a_powershell_file_test_is_not_a_check():
    # 3. A setup command that failed on a syntax error was "the latest check" because of the word "Test" in Test-Path.
    assert not _is_check_run(SETUP)
    assert not _is_check_run(r"Test-Path out\app.bin")


def test_a_real_test_next_to_a_file_test_is_still_a_check():
    assert _is_check_run("if (Test-Path tests) { python -m pytest -q }")
    assert _is_check_run("Test-Path dist; npm test")
