import re
import sys
import stat
import types
from pathlib import Path

from agent8088 import cli
from agent8088 import providers


class _Prompt:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _FakeInquirer:
    def __init__(self, responses):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls = []

    def _prompt(self, kind, kwargs):
        self.calls.append((kind, kwargs))
        return _Prompt(self.responses[kind].pop(0))

    def text(self, **kwargs):
        return self._prompt("text", kwargs)

    def secret(self, **kwargs):
        return self._prompt("secret", kwargs)

    def fuzzy(self, **kwargs):
        return self._prompt("fuzzy", kwargs)


def _install_fake_inquirer(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "InquirerPy", types.SimpleNamespace(inquirer=fake))


def test_data_files_are_not_duplicated_at_repo_root():
    """Data files must live ONLY in src/agent8088/.

    They used to be duplicated at the repo root, where nothing ever read them —
    edits there silently did nothing. This guards against the copies returning.
    """
    root = Path(__file__).resolve().parent.parent
    strays = [name for name in
              ("tools.txt", "system.md", "config.txt", "agents", "skills_installed")
              if (root / name).exists()]
    assert not strays, (
        f"these belong only in src/agent8088/, not the repo root: {strays}")


def test_packaged_data_files_exist():
    pkg = Path(__file__).resolve().parent.parent / "src" / "agent8088"
    for name in ("tools.txt", "system.md", "config.txt"):
        assert (pkg / name).is_file(), f"missing packaged data file: {name}"
    for name in ("agents", "skills_installed"):
        assert (pkg / name).is_dir(), f"missing packaged data dir: {name}"


def test_setup_hides_existing_key_and_url_defaults(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.txt"
    config.write_text(
        "\n".join([
            "allowed_paths=~",
            "default_provider=openai",
            "provider.openai.base_url=https://api.openai.com/v1",
            "provider.openai.model=gpt-4o",
            "provider.openai.api_key=sk-existing-secret",
            "search_base_url=http://private-search.local/search?q=",
        ]),
        encoding="utf-8",
    )
    fake = _FakeInquirer({
        "text": ["~", ""],
        "secret": [""],
        "fuzzy": ["openai", "gpt-live", "Keep current setting"],
    })
    _install_fake_inquirer(monkeypatch, fake)
    monkeypatch.setenv("AGENT8088_CONFIG", str(config))
    monkeypatch.setattr(providers, "list_models", lambda provider, client=None, fallback=True: ["gpt-live"])

    cli._run_setup()

    provider_prompt = [kwargs for kind, kwargs in fake.calls if kind == "fuzzy"][0]
    assert len(providers.builtin_provider_names()) == len(providers.BUILTIN_PROVIDERS)
    assert provider_prompt["choices"] == [*providers.builtin_provider_names(), cli.CUSTOM_PROVIDER_CHOICE]
    assert "default" not in provider_prompt

    secret_calls = [kwargs for kind, kwargs in fake.calls if kind == "secret"]
    assert secret_calls
    assert secret_calls[0].get("default") is None

    text_calls = [kwargs for kind, kwargs in fake.calls if kind == "text"]
    assert all(kwargs.get("default") != "http://private-search.local/search?q=" for kwargs in text_calls)

    saved = config.read_text(encoding="utf-8")
    assert "provider.openai.api_key=sk-existing-secret" in saved
    assert "provider.openai.model=gpt-live" in saved
    assert "search_base_url=http://private-search.local/search?q=" in saved

    output = capsys.readouterr().out
    assert "sk-existing-secret" not in output
    assert "http://private-search.local" not in output
    assert "gpt-live" not in output
    if sys.platform != "win32":
        assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_windows_installer_restricts_config_by_sid():
    installer = (Path(__file__).resolve().parent.parent / "install.ps1").read_text()
    assert "WindowsIdentity]::GetCurrent()" in installer
    assert 'icacls $Path /grant:r "*$sid`:(R,W)"' in installer
    assert installer.index("/grant:r") < installer.index("/inheritance:r")
    assert "$env:USERNAME`:(R,W)" not in installer


def test_setup_fetch_failure_asks_for_model_without_fallback_choices(tmp_path, monkeypatch):
    config = tmp_path / "config.txt"
    config.write_text(
        "\n".join([
            "allowed_paths=~",
            "default_provider=openai",
            "provider.openai.base_url=https://api.openai.com/v1",
            "provider.openai.api_key=sk-existing-secret",
        ]),
        encoding="utf-8",
    )
    fake = _FakeInquirer({
        "text": ["~", "typed-model", ""],
        "secret": [""],
        "fuzzy": ["openai", "None (disable web search)"],
    })
    _install_fake_inquirer(monkeypatch, fake)
    monkeypatch.setenv("AGENT8088_CONFIG", str(config))

    def fail(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(providers, "list_models", fail)

    cli._run_setup()

    fuzzy_calls = [kwargs for kind, kwargs in fake.calls if kind == "fuzzy"
                   and not str(kwargs.get("message", "")).startswith("Web search")]
    assert len(fuzzy_calls) == 1
    model_prompts = [
        kwargs for kind, kwargs in fake.calls
        if kind == "text" and kwargs.get("message") == "Model name:"
    ]
    assert model_prompts
    assert "provider.openai.model=typed-model" in config.read_text(encoding="utf-8")


def test_setup_custom_openai_compatible_provider(tmp_path, monkeypatch):
    config = tmp_path / "config.txt"
    config.write_text("allowed_paths=~\ndefault_provider=ollama\n", encoding="utf-8")
    fake = _FakeInquirer({
        "text": [
            "~", "invalid/name", "My Local AI", "",
            "https://llm.example.test/v1/chat/completions", "", "custom-model", "",
        ],
        "secret": ["secret-key"],
        "fuzzy": [cli.CUSTOM_PROVIDER_CHOICE, "None (disable web search)"],
    })
    _install_fake_inquirer(monkeypatch, fake)
    monkeypatch.setenv("AGENT8088_CONFIG", str(config))
    monkeypatch.setattr(providers, "list_models", lambda provider, client=None, fallback=True: [])

    cli._run_setup()

    saved = config.read_text(encoding="utf-8")
    assert "default_provider=my-local-ai" in saved
    assert "provider.my-local-ai.api_mode=openai" in saved
    assert "provider.my-local-ai.base_url=https://llm.example.test/v1" in saved
    assert "provider.my-local-ai.model=custom-model" in saved
    assert "provider.my-local-ai.api_key_env=MY_LOCAL_AI_API_KEY" in saved
    env_file = config.parent / ".env"
    if env_file.exists():
        env_content = env_file.read_text(encoding="utf-8")
        assert "MY_LOCAL_AI_API_KEY=secret-key" in env_content
    custom_prompts = [
        kwargs for kind, kwargs in fake.calls
        if kind == "text" and kwargs["message"] in {"Custom provider name:", "OpenAI-compatible URL:"}
    ]
    assert all("default" not in kwargs and "instruction" not in kwargs for kwargs in custom_prompts)
    assert len([call for call in custom_prompts if call["message"] == "Custom provider name:"]) == 2
    assert len([call for call in custom_prompts if call["message"] == "OpenAI-compatible URL:"]) == 2


def test_model_setup_custom_provider_stays_in_wizard(tmp_path, monkeypatch):
    config = tmp_path / "config.txt"
    config.write_text("allowed_paths=~\ndefault_provider=ollama\n", encoding="utf-8")
    fake = _FakeInquirer({
        "text": ["My REPL Provider", "https://llm.example.test/v1", "repl-model"],
        "secret": ["repl-key"],
        "fuzzy": [cli.CUSTOM_PROVIDER_CHOICE, "None (disable web search)"],
    })
    _install_fake_inquirer(monkeypatch, fake)
    monkeypatch.setattr(cli.A, "CONFIG_PATH", config)
    monkeypatch.setattr(providers, "list_models", lambda *_args, **_kwargs: [])
    activated = {}
    monkeypatch.setattr(
        cli,
        "_reload_model_runtime",
        lambda path, provider, model: activated.update(
            path=path, provider=provider, model=model
        ),
    )
    monkeypatch.setattr(cli, "banner", lambda: None)

    cli.cmd_model("setup")

    saved = config.read_text(encoding="utf-8")
    assert "default_provider=my-repl-provider" in saved
    assert "provider.my-repl-provider.model=repl-model" in saved
    assert activated == {
        "path": config,
        "provider": "my-repl-provider",
        "model": "repl-model",
    }


def test_model_setup_works_without_inquirerpy(tmp_path, monkeypatch):
    config = tmp_path / "config.txt"
    config.write_text("allowed_paths=~\ndefault_provider=ollama\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "InquirerPy", None)
    inputs = iter(["ollama-cloud", "glm-5.2:cloud"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda _: "cloud-key")
    monkeypatch.setattr(
        providers,
        "list_models",
        lambda provider, client=None, fallback=True: ["glm-5.2:cloud"],
    )

    cli._run_setup(config_path=config, include_workspace=False)

    saved = config.read_text(encoding="utf-8")
    assert "default_provider=ollama-cloud" in saved
    assert "provider.ollama-cloud.model=glm-5.2:cloud" in saved
    assert "provider.ollama-cloud.api_key_env=OLLAMA_CLOUD_API_KEY" in saved
    env_file = config.parent / ".env"
    if env_file.exists():
        env_content = env_file.read_text(encoding="utf-8")
        assert "OLLAMA_CLOUD_API_KEY=cloud-key" in env_content


def test_models_command_picks_and_switches_model(monkeypatch):
    fake = _FakeInquirer({
        "text": [],
        "secret": [],
        "fuzzy": ["openai", "gpt-new", "None (disable web search)"],
    })
    _install_fake_inquirer(monkeypatch, fake)
    monkeypatch.setattr(cli.A, "PROVIDERS", {"openai": {"model": "gpt-old", "base_url": "https://api.openai.com/v1"}})
    monkeypatch.setattr(cli, "_fetch_models_for_provider", lambda provider: ["gpt-new"])
    switched = {}
    monkeypatch.setattr(cli.A, "activate_model", lambda provider, model="": switched.update(provider=provider, model=model))

    cli.cmd_models("")

    assert switched == {"provider": "openai", "model": "gpt-new"}


def test_models_picker_works_without_inquirerpy(monkeypatch):
    monkeypatch.setitem(sys.modules, "InquirerPy", None)
    inputs = iter(["openai", "gpt-new"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(cli.A, "PROVIDERS", {"openai": {"model": "gpt-old"}})
    monkeypatch.setattr(cli.A, "APP_CONFIG", {})
    monkeypatch.setattr(cli, "_fetch_models_for_provider", lambda provider: ["gpt-new"])
    switched = {}
    monkeypatch.setattr(
        cli.A,
        "activate_model",
        lambda provider, model="": switched.update(provider=provider, model=model),
    )
    monkeypatch.setattr(cli, "banner", lambda: None)

    cli.cmd_models("")

    assert switched == {"provider": "openai", "model": "gpt-new"}


def test_model_command_prefers_configured_custom_provider(monkeypatch):
    monkeypatch.setattr(cli.A, "PROVIDERS", {"custom": {"model": "ornith-1.0-35b"}})
    monkeypatch.setattr(cli.A, "ACTIVE_PROVIDER", "")
    monkeypatch.setattr(cli.A, "MODEL_NAME", "")
    switched = {}
    redrawn = []
    monkeypatch.setattr(
        cli.A,
        "activate_model",
        lambda provider, model="": switched.update(provider=provider, model=model),
    )
    monkeypatch.setattr(cli, "banner", lambda: redrawn.append(True))

    cli.cmd_model("custom")

    assert switched == {"provider": "custom", "model": ""}
    assert redrawn == [True]


def test_model_command_supports_space_separated_switch(monkeypatch):
    monkeypatch.setattr(cli.A, "PROVIDERS", {"openai": {"model": "gpt-old"}})
    monkeypatch.setattr(cli.A, "ACTIVE_PROVIDER", "openai")
    monkeypatch.setattr(cli.A, "MODEL_NAME", "gpt-old")
    switched = {}
    monkeypatch.setattr(
        cli.A,
        "activate_model",
        lambda provider, model="": switched.update(provider=provider, model=model),
    )
    monkeypatch.setattr(cli, "banner", lambda: None)

    cli.cmd_model("openai gpt-new")

    assert switched == {"provider": "openai", "model": "gpt-new"}


def test_list_models_can_disable_hardcoded_fallbacks(monkeypatch):
    monkeypatch.setattr(providers, "_load_disk_cache", lambda: {})
    monkeypatch.setattr(providers, "_save_disk_cache", lambda data: None)

    class BrokenClient:
        class models:
            @staticmethod
            def list():
                raise RuntimeError("offline")

    assert providers.list_models("openai", client=BrokenClient(), fallback=False) == []
    assert providers.list_models("openai", client=BrokenClient(), fallback=True)


def test_models_custom_connects_openai_compatible_endpoint(monkeypatch):
    fake = _FakeInquirer({
        "text": ["http://127.0.0.1:8080/v1/chat/completions", "ornith-1.0-35b"],
        "secret": ["sk-local"],
        "fuzzy": [],
    })
    _install_fake_inquirer(monkeypatch, fake)

    class FakeEngine:
        APP_CONFIG = {}
        PROVIDERS = {}
        ACTIVE_PROVIDER = ""
        MODEL_NAME = ""
        client = None

        @classmethod
        def get_client(cls, provider):
            return {"provider": provider}, cls.PROVIDERS[provider]["model"]

        @classmethod
        def activate_model(cls, provider, model=""):
            cls.client, cls.MODEL_NAME = cls.get_client(provider)
            cls.MODEL_NAME = model or cls.MODEL_NAME
            cls.ACTIVE_PROVIDER = provider

    monkeypatch.setattr(cli, "A", FakeEngine)
    monkeypatch.setattr(cli, "banner", lambda: None)
    cli.cmd_models("custom")

    assert FakeEngine.PROVIDERS["custom"] == {
        "api_mode": "openai",
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "ornith-1.0-35b",
        "api_key": "sk-local",
    }
    assert FakeEngine.ACTIVE_PROVIDER == "custom"
    assert FakeEngine.MODEL_NAME == "ornith-1.0-35b"
    assert all("default" not in kwargs for kind, kwargs in fake.calls if kind == "text")
    assert [kwargs for kind, kwargs in fake.calls if kind == "secret"] == [{"message": "API key:"}]


def test_models_custom_works_without_inquirerpy(monkeypatch):
    monkeypatch.setitem(sys.modules, "InquirerPy", None)
    inputs = iter(["http://127.0.0.1:8080/v1/chat/completions", "ornith-1.0-35b"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda _: "sk-local")

    class FakeEngine:
        APP_CONFIG = {}
        PROVIDERS = {}
        ACTIVE_PROVIDER = ""
        MODEL_NAME = ""
        client = None

        @classmethod
        def get_client(cls, provider):
            return {"provider": provider}, cls.PROVIDERS[provider]["model"]

        @classmethod
        def activate_model(cls, provider, model=""):
            cls.client, cls.MODEL_NAME = cls.get_client(provider)
            cls.MODEL_NAME = model or cls.MODEL_NAME
            cls.ACTIVE_PROVIDER = provider

    monkeypatch.setattr(cli, "A", FakeEngine)
    monkeypatch.setattr(cli, "banner", lambda: None)
    cli.cmd_models("custom")

    assert FakeEngine.PROVIDERS["custom"]["base_url"] == "http://127.0.0.1:8080/v1"
    assert FakeEngine.PROVIDERS["custom"]["api_key"] == "sk-local"


# ---------------------------------------------------------------------------
# keys introduced after a config was written
#
# Setup edits the config in place, so it can only ever update keys that are
# already present. Observed live: a config predating web_search_no_prompt kept
# defaulting to 0 while a fresh install shipped 1, so every search against a
# working local SearXNG raised an approval prompt — and re-running setup, the
# obvious remedy, could never add the missing key.
# ---------------------------------------------------------------------------
def _run_setup_over(config, monkeypatch, extra_lines=""):
    config.write_text(
        "\n".join([
            "allowed_paths=~",
            "default_provider=openai",
            "provider.openai.base_url=https://api.openai.com/v1",
            "provider.openai.model=gpt-4o",
            "provider.openai.api_key=sk-placeholder-not-a-real-key",
            "search_base_url=http://127.0.0.1:8888/search?q=",
        ]) + extra_lines,
        encoding="utf-8",
    )
    _install_fake_inquirer(monkeypatch, _FakeInquirer({
        "text": ["~", ""],
        "secret": [""],
        "fuzzy": ["openai", "gpt-live", "Keep current setting"],
    }))
    monkeypatch.setenv("AGENT8088_CONFIG", str(config))
    monkeypatch.setattr(providers, "list_models",
                        lambda provider, client=None, fallback=True: ["gpt-live"])
    cli._run_setup()
    return config.read_text(encoding="utf-8")


def _packaged_no_prompt():
    packaged = (Path(__file__).resolve().parent.parent
                / "src" / "agent8088" / "config.txt").read_text(encoding="utf-8")
    return re.search(r'^\s*web_search_no_prompt=(.*)$',
                     packaged, re.MULTILINE).group(1).strip()


def test_setup_backfills_a_config_written_before_the_key_existed(tmp_path, monkeypatch):
    saved = _run_setup_over(tmp_path / "config.txt", monkeypatch)

    assert f"web_search_no_prompt={_packaged_no_prompt()}" in saved


def test_the_backfilled_value_tracks_the_packaged_template(tmp_path, monkeypatch):
    """Hardcoding the default here would let setup and the template drift."""
    saved = _run_setup_over(tmp_path / "config.txt", monkeypatch)

    match = re.search(r'^\s*web_search_no_prompt=(.*)$', saved, re.MULTILINE)
    assert match.group(1).strip() == _packaged_no_prompt()


def test_setup_does_not_overrule_a_deliberate_opt_out(tmp_path, monkeypatch):
    """Backfill fills a gap; it must not reinstate a prompt the user turned off."""
    saved = _run_setup_over(tmp_path / "config.txt", monkeypatch,
                            extra_lines="\nweb_search_no_prompt=0\n")

    assert "web_search_no_prompt=0" in saved
    assert "web_search_no_prompt=1" not in saved


def test_the_backfill_is_announced_rather_than_silent(tmp_path, monkeypatch, capsys):
    """It relaxes an approval gate, so it must not happen without a word."""
    _run_setup_over(tmp_path / "config.txt", monkeypatch)

    assert "web_search_no_prompt" in capsys.readouterr().out
