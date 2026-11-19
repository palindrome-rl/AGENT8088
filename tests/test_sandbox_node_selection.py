"""Setup and runtime must agree on a supported Windows Node executable."""
import hashlib
import io
import zipfile
import pytest
from agent8088 import engine


@pytest.fixture
def nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    monkeypatch.delenv("AGENT8088_SRT", raising=False)
    managed = tmp_path / "node" / "node.exe"
    path_node = tmp_path / "system" / "node.exe"
    for node in (managed, path_node):
        node.parent.mkdir()
        node.write_text("")
        node.with_name("npm.cmd").write_text("")
    runner = tmp_path / "runner.js"
    runner.write_text("")
    monkeypatch.setattr(engine, "_dsh_runner_path", lambda: runner)
    monkeypatch.setattr(engine, "_which_executable", lambda name: str(path_node) if name == "node" else None)
    return managed, path_node


@pytest.mark.parametrize("managed_version,path_version,selected", [
    ("v18.12.1", "v22.11.0", 1),
    ("v22.11.0", "v18.12.1", 0),
    ("broken", "v22.11.0", 1),
    ("v20.10.0", "v20.11.0", 1),
    ("v18.12.1", "v20.10.0", None),
])
def test_runtime_and_setup_select_same_supported_node(nodes, monkeypatch,
                                                     managed_version, path_version, selected):
    monkeypatch.setattr(engine.subprocess, "run", lambda argv, **kw: type(
        "Result", (), {"stdout": managed_version if argv[0] == str(nodes[0]) else path_version,
                        "returncode": 0})())
    setup_node, _, _ = engine._native_sandbox_node_toolchain()
    runtime = engine._native_sandbox_argv()
    expected = str(nodes[selected]) if selected is not None else None
    assert setup_node == expected
    assert (runtime[0] if runtime else None) == expected


def test_runtime_does_not_need_npm(nodes, monkeypatch):
    nodes[0].with_name("npm.cmd").unlink()
    monkeypatch.setattr(engine.subprocess, "run", lambda *a, **kw: type(
        "Result", (), {"stdout": "v22.11.0", "returncode": 0})())
    assert engine._native_sandbox_argv()[0] == str(nodes[0])


def test_failed_version_probe_is_not_accepted(nodes, monkeypatch):
    monkeypatch.setattr(engine.subprocess, "run", lambda *a, **kw: type(
        "Result", (), {"stdout": "v22.11.0", "returncode": 1})())
    assert engine._native_sandbox_node_toolchain()[0] is None
    assert engine._native_sandbox_argv() is None


@pytest.mark.parametrize("problem", [None, "checksum", "missing-npm"])
def test_managed_download_checks_payload_before_replacing_existing_node(tmp_path, monkeypatch, problem):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    monkeypatch.setattr(engine.platform, "machine", lambda: "AMD64")
    name = f"node-v{engine._MANAGED_NODE_VERSION}-win-x64"
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(f"{name}/node.exe", b"new-node")
        if problem != "missing-npm":
            archive.writestr(f"{name}/npm.cmd", b"new-npm")
    payload = data.getvalue()
    digest = "0" * 64 if problem == "checksum" else hashlib.sha256(payload).hexdigest()
    def download(url, **kwargs):
        return io.BytesIO(f"{digest}  {name}.zip\n".encode() if url.endswith("SHASUMS256.txt") else payload)
    monkeypatch.setattr(engine.urllib.request, "urlopen", download)
    node = engine._managed_node_path()
    node.parent.mkdir()
    node.write_bytes(b"old-node")
    result = engine._install_managed_node()
    if problem:
        assert result and "not installing" in result
        assert node.read_bytes() == b"old-node"
    else:
        assert result is None
        assert node.read_bytes() == b"new-node"
        assert node.with_name("npm.cmd").read_bytes() == b"new-npm"
