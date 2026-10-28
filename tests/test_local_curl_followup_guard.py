"""A shell call to a service on this machine is not an unsolicited web fetch after a search.

Seen in a demo run: after one web search, the agent's `curl http://localhost:11434/api/tags` (to check which models the local Ollama server
has, which the task needed) was refused three times with "Follow-up fetch was not run - the search already answered this". The repeated
refusals fed the repeat breaker, which ended the run at turn 218 of 400. Real web fetches after a search are still refused."""
import pytest

MESSAGES = [{"role": "user", "content": "Create a Pokemon Doubles battle simulator web app using the tev1 model on Ollama."}]


def _followup(engine, command):
    return engine._is_fetch_followup(MESSAGES, "execute_shell", {"command": command})


@pytest.mark.parametrize("command", [
    "curl -s http://localhost:11434/api/tags",
    "curl -s http://localhost:11434/api/tags -o tags.json && type tags.json",
    "curl -s http://127.0.0.1:8765/index.html",
    "curl http://[::1]:8000/health",
    "curl -s http://192.168.10.20:8000/v1/models",
    "curl http://10.0.0.5/status",
    "curl http://172.16.4.2:9000/",
    "wget -qO- http://app.localhost:3000/",
    'curl -s -X POST http://localhost:11434/v1/systemone -d "{}"',
    "curl -s http://localhost:11434/api/tags http://127.0.0.1:8765/",
])
def test_a_call_to_a_local_service_is_not_a_search_follow_up(engine, command):
    assert not _followup(engine, command)


@pytest.mark.parametrize("command", [
    "curl -s https://pokeapi.co/api/v2/pokemon/garchomp",
    "wget https://example.com/page.html",
    "curl -sL https://game8.co/games/Pokemon-Champions",
    "curl http://localhost.evil.com/x",              # a lookalike name is not localhost
    "curl http://127.0.0.1.evil.com/x",
    "curl http://localhost:11434/api/tags https://example.com/",   # one remote URL among local ones
    "curl -s http://8.8.8.8/",                       # a public address
    "curl",                                          # fetch-shaped, no explicit URL: unchanged
])
def test_a_real_web_fetch_after_a_search_is_still_refused(engine, command):
    assert _followup(engine, command)


def test_commands_that_are_not_fetches_are_unaffected(engine):
    for command in ("python app.py", "node --check app.js", "dir", "which curl"):
        assert not _followup(engine, command)


def test_the_url_helper_is_judged_by_the_host_as_written(engine):
    assert engine._is_local_url("http://localhost:11434/api/tags")
    assert engine._is_local_url("http://192.168.10.20:8000/v1")
    assert not engine._is_local_url("https://localhost.example.com/")
    assert not engine._is_local_url("not a url")
    assert not engine._is_local_url("http:///nohost")


@pytest.mark.parametrize("command", [
    "curl http://[broken]/",
    "curl http://localhost:bad/",
    "curl http://localhost:9000/ && curl example.com",
    "curl http://localhost:9000/ --next example.com",
    "curl http://localhost:9000/ --url example.com",
    "curl http://localhost:9000/ -K config.txt",
    'curl http://localhost:9000/ "$REMOTE_URL"',
    "curl http://localhost:9000/ && wget",
])
def test_malformed_or_unproven_targets_keep_the_followup_guard(engine, command):
    assert _followup(engine, command)
