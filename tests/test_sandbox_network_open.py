"""sandbox_network=open: sandboxed commands reach the computer's services.

Issue sheet row 46: the native Linux sandbox has no network namespace of its own,
so IMAP and database clients on localhost were unreachable. With the setting on,
auto mode runs commands in Docker on the host network instead.
"""
import pytest


@pytest.fixture
def backend(engine, monkeypatch):
    def resolve(open_net, requested="auto", enforces=True, docker=True, native=True):
        monkeypatch.setattr(engine, "SANDBOX_NETWORK_OPEN", open_net)
        monkeypatch.setattr(engine, "SANDBOX_BACKEND", requested)
        monkeypatch.setattr(engine, "_native_enforces_network", lambda: enforces)
        monkeypatch.setattr(engine, "_docker_usable", lambda: docker)
        monkeypatch.setattr(engine, "_native_sandbox_broken", False)
        monkeypatch.setattr(engine, "_native_sandbox_missing_requirements",
                            lambda: [] if native else ["sandbox-runtime"])
        return engine._resolve_sandbox_backend()
    return resolve


def test_open_network_moves_linux_and_macos_commands_to_docker(backend):
    assert backend(open_net=True) == "docker"


def test_closed_network_keeps_the_native_sandbox(backend):
    assert backend(open_net=False) == "native"


def test_windows_native_is_already_open_so_it_stays(backend):
    assert backend(open_net=True, enforces=False) == "native"


def test_without_docker_the_native_sandbox_still_runs(backend):
    assert backend(open_net=True, docker=False) == "native"


def test_an_explicit_native_backend_is_respected(backend):
    assert backend(open_net=True, requested="native") == "native"


@pytest.mark.parametrize("platform, network", [("linux", "host"), ("darwin", "bridge")])
def test_open_network_picks_an_unfiltered_docker_network(engine, monkeypatch, platform, network):
    monkeypatch.setattr("sys.platform", platform)
    assert engine._open_docker_network() == network
    monkeypatch.setattr(engine, "DOCKER_NETWORK", network)
    assert engine._docker_egress_args() == (network, [])
    assert not engine._docker_network_closed()
    assert "not filtered" in engine._sandbox_network_summary("docker")
