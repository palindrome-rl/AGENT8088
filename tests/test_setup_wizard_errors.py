"""The setup wizard's failure paths: what it says when discovery or the first
request fails, what Ctrl+C does, and the search-provider bug that wrote an
ExaProvider object into default_provider.

Every prompt, the model endpoint and Docker are mocked; config and .env live
in tmp_path.
"""
import sys
import types

import pytest

from agent8088 import cli


class _Status(Exception):
    def __init__(self, status, message="error"):
        super().__init__(message)
        self.status_code = status


def _wizard(monkeypatch, tmp_path, choices, customs=None, discover=None, test_call=None,
            docker=False):
    """Drive _run_setup with scripted answers. choices/customs map a prompt
    prefix to an answer (or a list of answers, consumed in order)."""
    customs = customs or {}
    seen = {"choice": [], "custom": [], "test_calls": []}

    def answer(table, message, fallback):
        for prefix, value in table.items():
            if message.startswith(prefix):
                if isinstance(value, list):
                    return value.pop(0)
                return value
        return fallback

    def choice(message, options, default=""):
        seen["choice"].append((message, list(options)))
        picked = answer(choices, message, None)
        if picked is None:
            return default or options[0]
        if picked not in options:
            matches = [o for o in options if o.lower().startswith(picked.lower())]
            assert matches, (picked, options)
            picked = matches[0]
        return picked

    def custom(message, default="", secret=False, instruction=""):
        seen["custom"].append(message)
        return answer(customs, message, default)

    def fake_test_call(base_url, api_key, model):
        seen["test_calls"].append((base_url, model))
        return test_call(base_url, model) if test_call else None

    monkeypatch.setattr(cli, "_choice_prompt", choice)
    monkeypatch.setattr(cli, "_custom_prompt", custom)
    monkeypatch.setattr(cli, "_setup_discover_models",
                        discover or (lambda provider, base_url, key: (["m1", "m2"], None)))
    monkeypatch.setattr(cli, "_setup_test_call", fake_test_call)
    monkeypatch.setattr(cli, "_embedding_model_present", lambda: False)
    monkeypatch.setattr(cli.A, "_docker_available", lambda: docker)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(cli.A, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(cli.A, "load_env_file", lambda path=None: {})
    written_env = {}
    monkeypatch.setattr(cli.A, "update_env_file", lambda path, values: written_env.update(values))
    seen["env"] = written_env
    return seen


def _config(tmp_path):
    return tmp_path / "config.txt"


def test_picking_a_keyed_search_backend_keeps_the_model_provider(monkeypatch, tmp_path):
    """Regression: the search loop reused `provider`, so default_provider was
    written as '<ExaProvider object ...>' and the key env var crashed."""
    seen = _wizard(monkeypatch, tmp_path,
                   choices={"Select model provider": "openai", "Select model": "m1",
                            "Web search": "exa"},
                   customs={"Working directory": ".", "API key for": "sk-test",
                            "Exa": "exa-key"})
    cli._run_setup(config_path=_config(tmp_path))
    text = _config(tmp_path).read_text()
    assert "default_provider=openai\n" in text
    assert "Provider object" not in text
    assert "provider.openai.model=m1" in text
    assert seen["env"].get("EXA_API_KEY") == "exa-key"
    assert seen["env"].get("OPENAI_API_KEY") == "sk-test"
    assert "web_search_provider=exa" in text


def test_discovery_failure_says_why(monkeypatch, tmp_path, capsys):
    def discover(provider, base_url, key):
        return [], _Status(401, "invalid api key")

    _wizard(monkeypatch, tmp_path,
            choices={"Select model provider": "openai", "Web search": "ddgs"},
            customs={"Working directory": ".", "API key for": "sk-bad", "Model name": "gpt-x"},
            discover=discover)
    cli._run_setup(config_path=_config(tmp_path))
    out = capsys.readouterr().out
    assert "Model discovery failed:" in out
    assert "API key rejected" in out and "OPENAI_API_KEY" in out
    assert "provider.openai.model=gpt-x" in _config(tmp_path).read_text()


def test_a_404_without_v1_retries_with_v1_and_offers_it(monkeypatch, tmp_path, capsys):
    calls = []

    def discover(provider, base_url, key):
        calls.append(base_url)
        if base_url.endswith("/v1"):
            return ["served-model"], None
        return [], _Status(404, "not found")

    _wizard(monkeypatch, tmp_path,
            choices={"Select model provider": cli.CUSTOM_PROVIDER_CHOICE, "Use http": "Yes",
                     "Select model": "served-model", "Web search": "ddgs"},
            customs={"Working directory": ".", "Custom provider name": "myvllm",
                     "OpenAI-compatible URL": "http://box:8000", "API key for": ""},
            discover=discover)
    cli._run_setup(config_path=_config(tmp_path))
    assert calls == ["http://box:8000", "http://box:8000/v1"]
    text = _config(tmp_path).read_text()
    assert "provider.myvllm.base_url=http://box:8000/v1" in text
    assert "provider.myvllm.model=served-model" in text


def test_failed_test_call_can_switch_model_then_succeed(monkeypatch, tmp_path, capsys):
    def test_call(base_url, model):
        return _Status(404, f"model '{model}' not found") if model == "m1" else None

    seen = _wizard(monkeypatch, tmp_path,
                   choices={"Select model provider": "openai", "Select model": "m1",
                            "What now?": "Enter a different model", "Web search": "ddgs"},
                   customs={"Working directory": ".", "API key for": "sk", "Model name": "m2"},
                   test_call=test_call)
    cli._run_setup(config_path=_config(tmp_path))
    out = capsys.readouterr().out
    assert "Test failed:" in out and "doesn't have the model 'm1'" in out
    assert [model for _, model in seen["test_calls"]] == ["m1", "m2"]
    assert "provider.openai.model=m2" in _config(tmp_path).read_text()
    assert "Setup complete." in out


def test_failed_test_call_save_anyway_still_writes(monkeypatch, tmp_path, capsys):
    _wizard(monkeypatch, tmp_path,
            choices={"Select model provider": "openai", "Select model": "m1",
                     "What now?": "Save anyway", "Web search": "ddgs"},
            customs={"Working directory": ".", "API key for": "sk"},
            test_call=lambda base_url, model: ConnectionRefusedError("connection refused"))
    cli._run_setup(config_path=_config(tmp_path))
    assert "provider.openai.model=m1" in _config(tmp_path).read_text()


def test_cancel_after_a_failed_test_call_writes_nothing(monkeypatch, tmp_path):
    seen = _wizard(monkeypatch, tmp_path,
                   choices={"Select model provider": "openai", "Select model": "m1",
                            "What now?": "Cancel"},
                   customs={"Working directory": ".", "API key for": "sk"},
                   test_call=lambda base_url, model: _Status(401, "bad key"))
    with pytest.raises(KeyboardInterrupt):
        cli._run_setup(config_path=_config(tmp_path))
    assert not _config(tmp_path).exists()
    assert seen["env"] == {}


def test_a_slow_local_model_is_saved_with_a_note(monkeypatch, tmp_path, capsys):
    _wizard(monkeypatch, tmp_path,
            choices={"Select model provider": "ollama", "Select model": "m1", "Web search": "ddgs"},
            customs={"Working directory": "."},
            test_call=lambda base_url, model: TimeoutError("timed out"))
    cli._run_setup(config_path=_config(tmp_path))
    assert "may still be loading" in capsys.readouterr().out
    assert "provider.ollama.model=m1" in _config(tmp_path).read_text()


def test_searxng_without_docker_says_ddgs_is_used(monkeypatch, tmp_path, capsys):
    started = []
    monkeypatch.setattr(cli.searxng_provision, "start", lambda *a, **k: started.append(1))
    seen = _wizard(monkeypatch, tmp_path,
                   choices={"Select model provider": "ollama", "Select model": "m1",
                            "Web search": "SearXNG (needs Docker"},
                   customs={"Working directory": "."}, docker=False)
    cli._run_setup(config_path=_config(tmp_path))
    out = capsys.readouterr().out
    assert "Docker isn't available" in out
    assert "ddgs" in out and "less reliable" in out
    assert started == []
    search_options = next(opts for msg, opts in seen["choice"] if msg.startswith("Web search"))
    assert any(o.startswith("SearXNG (needs Docker") for o in search_options)


# --- Ctrl+C ------------------------------------------------------------------

def _fake_inquirer(monkeypatch, exc):
    class _Prompt:
        def __init__(self, **kwargs):
            pass

        def execute(self):
            raise exc

    inquirer = types.SimpleNamespace(text=_Prompt, secret=_Prompt, fuzzy=_Prompt,
                                     checkbox=_Prompt, number=_Prompt)
    module = types.ModuleType("InquirerPy")
    module.inquirer = inquirer
    monkeypatch.setitem(sys.modules, "InquirerPy", module)


def test_ctrl_c_in_a_prompt_is_not_turned_into_a_text_fallback(monkeypatch):
    _fake_inquirer(monkeypatch, KeyboardInterrupt())
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("fell back to input()"))
    with pytest.raises(KeyboardInterrupt):
        cli._custom_prompt("Model name:")
    with pytest.raises(KeyboardInterrupt):
        cli._choice_prompt("Pick:", ["a", "b"])


def test_a_broken_inquirerpy_still_falls_back_to_input(monkeypatch):
    _fake_inquirer(monkeypatch, OSError("no console"))
    monkeypatch.setattr("builtins.input", lambda *a: "typed")
    assert cli._custom_prompt("Model name:") == "typed"


def test_setup_cancelled_with_ctrl_c_exits_130_and_writes_nothing(monkeypatch, tmp_path, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(cli, "_resolve_config_path", lambda: config)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_choice_prompt", interrupted)
    monkeypatch.setattr(cli, "_custom_prompt", lambda message, default="", **k: default)
    monkeypatch.setattr(sys, "argv", ["agent8088", "--setup"])
    assert cli.main() == 130
    assert "Setup cancelled — nothing was written." in capsys.readouterr().out
    assert not config.exists()
