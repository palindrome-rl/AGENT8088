"""Offline public acceptance checks; no services, credentials or system changes."""
import importlib
from types import SimpleNamespace as N

import pytest


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "absent.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "data"))
    from agent8088 import capabilities, engine
    engine = importlib.reload(engine)
    capabilities.reset()
    yield engine
    capabilities.reset()


def test_plan_search_grant_is_one_shot_and_does_not_allow_writes(runtime):
    runtime.set_permission_mode("plan-only")
    runtime._one_shot_grants.add("approved-search")
    assert runtime.check_permission("search", approval_key="approved-search")
    assert not runtime.check_permission("search", approval_key="approved-search")
    assert not runtime.check_permission("write_text")


def test_explicit_root_output_is_not_relocated(tmp_path):
    from agent8088 import completion
    artifacts = tmp_path / "artifacts"
    (artifacts / "logs").mkdir(parents=True)
    misplaced = artifacts / "logs" / "server.js"
    misplaced.write_text("console.log('ok')", encoding="utf-8")
    request = "Create ./server.js at the workspace root. Write logs/debug.txt."
    missing = completion.missing_outputs(request, "done", [str(misplaced)], [tmp_path], [artifacts])
    assert any("server.js" in item for item in missing)


def test_requested_project_folder_satisfies_short_output_name(tmp_path):
    from agent8088 import completion
    artifacts = tmp_path / "artifacts"
    (artifacts / "project").mkdir(parents=True)
    output = artifacts / "project" / "server.js"
    output.write_text("console.log('ok')", encoding="utf-8")
    assert completion.missing_outputs("Build the app in project/. Create server.js.", "done",
                                      [str(output)], [tmp_path], [artifacts]) == []


@pytest.mark.parametrize("path", ["/api-client.json", "/apiary/data.json", "/apiculture.js"])
def test_api_prefixed_asset_is_not_a_backend_route(path):
    from agent8088 import app_check
    assert not app_check._is_route(path)
    assert app_check._is_route("/api/status")


def test_package_description_does_not_imply_backend(tmp_path):
    from agent8088 import app_check
    (tmp_path / "package.json").write_text('{"description":"start"}', encoding="utf-8")
    assert not app_check.has_backend(tmp_path)


def test_activated_project_is_preserved_with_an_installed_interpreter(runtime, tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(runtime.sys, "prefix", str(data / "agent8088" / "venv"))
    monkeypatch.setattr(runtime.sys, "base_prefix", str(tmp_path / "python"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "project" / ".venv"))
    assert runtime._project_venv_active()
    assert not runtime._own_install_prefix(data / "projects" / "example" / ".venv")


@pytest.mark.parametrize("returncode,expected", [(0, False), (1, None)])
def test_failed_outer_probe_does_not_claim_child_success(runtime, tmp_path, monkeypatch, returncode, expected):
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime, "_native_sandbox_argv", lambda: ["runtime"])
    monkeypatch.setattr(runtime, "_native_sandbox_node_toolchain", lambda **kwargs: ("node", None, "22"))
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: N(
        returncode=returncode, stdout="agent8088-piped-spawn-ok", stderr=""))
    runtime._probe_piped_child_spawn(tmp_path)
    assert runtime._native_piped_spawn_blocked is expected


def test_working_custom_embedding_transport_is_not_vetoed(monkeypatch):
    import httpx
    from openai import OpenAI
    from agent8088.memory import embed
    calls = []

    def respond(request):
        calls.append(request.url)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    def no_direct_probe(*args):
        raise AssertionError("custom transports must not be tested through a direct TCP connection")

    monkeypatch.setattr(embed, "_reachable", no_direct_probe)
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = OpenAI(api_key="synthetic-offline-check", base_url="https://embeddings.example.invalid/v1",
                        http_client=transport)
        assert embed.Embedder(lambda: client, "test-model", provider="custom").embed(["test"]) == [[1.0, 0.0]]
    assert len(calls) == 1
