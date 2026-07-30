import sys
import types

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
        "fuzzy": ["openai", "gpt-live"],
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
        "fuzzy": ["openai"],
    })
    _install_fake_inquirer(monkeypatch, fake)
    monkeypatch.setenv("AGENT8088_CONFIG", str(config))

    def fail(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(providers, "list_models", fail)

    cli._run_setup()

    fuzzy_calls = [kwargs for kind, kwargs in fake.calls if kind == "fuzzy"]
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
        "text": ["~", "localai", "https://llm.example.test/v1", "custom-model", ""],
        "secret": ["secret-key"],
        "fuzzy": [cli.CUSTOM_PROVIDER_CHOICE],
    })
    _install_fake_inquirer(monkeypatch, fake)
    monkeypatch.setenv("AGENT8088_CONFIG", str(config))
    monkeypatch.setattr(providers, "list_models", lambda provider, client=None, fallback=True: [])

    cli._run_setup()

    saved = config.read_text(encoding="utf-8")
    assert "default_provider=localai" in saved
    assert "provider.localai.api_mode=openai" in saved
    assert "provider.localai.base_url=https://llm.example.test/v1" in saved
    assert "provider.localai.model=custom-model" in saved
    assert "provider.localai.api_key=secret-key" in saved


def test_models_command_picks_and_switches_model(monkeypatch):
    fake = _FakeInquirer({
        "text": [],
        "secret": [],
        "fuzzy": ["openai", "gpt-new"],
    })
    _install_fake_inquirer(monkeypatch, fake)
    monkeypatch.setattr(cli.A, "PROVIDERS", {"openai": {"model": "gpt-old", "base_url": "https://api.openai.com/v1"}})
    monkeypatch.setattr(cli, "_fetch_models_for_provider", lambda provider: ["gpt-new"])
    switched = {}
    monkeypatch.setattr(cli.A, "activate_model", lambda provider, model="": switched.update(provider=provider, model=model))

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
