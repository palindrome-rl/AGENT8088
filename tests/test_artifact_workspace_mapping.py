"""Container path aliases must not rewrite real host workspace paths."""
import pytest


@pytest.mark.parametrize("path", [
    "/private/tmp/test/workspace/artifacts/allowed.txt",
    "/home/alice/workspace/artifacts/file.txt",
    "C:/workspace/artifacts/file.txt",
])
def test_absolute_host_artifact_paths_are_preserved(engine, path):
    command = f"cat '{path}'"
    assert engine._artifact_workspace_command(command) == command


def test_container_artifact_alias_still_maps_to_the_mount(engine):
    assert engine._artifact_workspace_command("cat '/workspace/artifacts/file.txt'") == "cat '/workspace/file.txt'"


# --- a command that changes directory first keeps its relative paths as written ----------------------------

@pytest.mark.parametrize("command", [
    r'cd "E:\Palindrome Labs\runs\pw5\workspace" && python artifacts\outputs\verify.py',
    r"cd /d E:\proj\workspace && dir artifacts",
    "cd /home/alice/project && python artifacts/outputs/verify.py",
    "cd .. && python artifacts/run.py",
    r"cd sub && type artifacts\notes.txt",
    r"pushd C:\work && python artifacts\x.py",
])
def test_after_a_cd_elsewhere_the_artifacts_path_is_left_alone(engine, command):
    assert engine._artifact_workspace_command(command) == command


@pytest.mark.parametrize("command, expected", [
    (r"python artifacts\x.py", "python ./x.py"),
    ("dir artifacts", "dir ."),
    ("cd artifacts && python x.py", "cd . && python x.py"),
    (r"cd artifacts\sub && python x.py", "cd ./sub && python x.py"),
    (r"cd E:\proj\workspace\artifacts && python run.py", r"cd E:\proj\workspace\artifacts && python run.py"),
    (r"python artifacts\a.py && cd E:\proj && python artifacts\b.py", r"python ./a.py && cd E:\proj && python artifacts\b.py"),
])
def test_without_a_cd_elsewhere_the_mapping_is_unchanged(engine, command, expected):
    assert engine._artifact_workspace_command(command) == expected


def test_the_container_alias_is_mapped_even_after_a_cd(engine):
    assert engine._artifact_workspace_command("cd /workspace && cat /workspace/artifacts/f.txt") \
        == "cd /workspace && cat /workspace/f.txt"
