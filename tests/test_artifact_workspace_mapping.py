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
