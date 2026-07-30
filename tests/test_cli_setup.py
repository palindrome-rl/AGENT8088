import sys
import types


class Prompt:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeInquirer:
    def __init__(self):
        self.select_kwargs = None
        self.fuzzy_kwargs = None

    def text(self, **kwargs):
        if kwargs["message"] == "Working directory:":
            return Prompt("~")
        if kwargs["message"] == "Web search URL (SearXNG):":
            return Prompt("")
        return Prompt(kwargs.get("default", ""))

    def secret(self, **kwargs):
        return Prompt("")

    def select(self, **kwargs):
        self.select_kwargs = kwargs
        return Prompt("copilot")

    def fuzzy(self, **kwargs):
        self.fuzzy_kwargs = kwargs
        return Prompt("gpt-4o-mini")


class CustomEndpointInquirer:
    def __init__(self):
        self.text_kwargs = []
        self.secret_kwargs = []

    def text(self, **kwargs):
        self.text_kwargs.append(kwargs)
        if kwargs["message"] == "OpenAI-compatible URL:":
            return Prompt("http://127.0.0.1:8080/v1/chat/completions")
        if kwargs["message"] == "Model:":
            return Prompt("ornith-1.0-35b")
        return Prompt(kwargs.get("default", ""))

    def secret(self, **kwargs):
        self.secret_kwargs.append(kwargs)
        return Prompt("sk-local")


def test_setup_shows_provider_menu_and_fetches_models(tmp_path, monkeypatch):
    config = tmp_path / "config.txt"
    config.write_text("allowed_paths=~\ndefault_provider=ollama\n", encoding="utf-8")
    monkeypatch.setenv("AGENT8088_CONFIG", str(config))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))

    fake = FakeInquirer()
    monkeypatch.setitem(sys.modules, "InquirerPy", types.SimpleNamespace(inquirer=fake))

    class Model:
        def __init__(self, model_id):
            self.id = model_id

    class Models:
        def list(self):
            return types.SimpleNamespace(data=[Model("gpt-4o-mini"), Model("gpt-4o")])

    class OpenAI:
        def __init__(self, **kwargs):
            self.base_url = kwargs.get("base_url", "")
            self.models = Models()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=OpenAI))

    from agent8088.cli import _run_setup

    _run_setup()

    values = [choice["value"] for choice in fake.select_kwargs["choices"]]
    assert values == [
        "ollama", "openrouter", "openai", "gemini", "cerebras", "deepseek",
        "groq", "mistral", "moonshot", "qwen", "ollama-cloud", "copilot",
        "__custom__",
    ]
    names = [choice["name"] for choice in fake.select_kwargs["choices"]]
    assert "GitHub Copilot (copilot) - default: gpt-4o-mini" in names
    assert all("anthropic)" not in name.lower() for name in names)
    assert fake.fuzzy_kwargs["choices"] == ["gpt-4o", "gpt-4o-mini"]
    saved = config.read_text(encoding="utf-8")
    assert "default_provider=copilot" in saved
    assert "provider.copilot.model=gpt-4o-mini" in saved


def test_models_custom_connects_openai_compatible_endpoint(monkeypatch):
    fake = CustomEndpointInquirer()
    monkeypatch.setitem(sys.modules, "InquirerPy", types.SimpleNamespace(inquirer=fake))

    from agent8088 import cli

    class FakeEngine:
        APP_CONFIG = {}
        PROVIDERS = {}
        ACTIVE_PROVIDER = ""
        MODEL_NAME = ""
        client = None

        @classmethod
        def get_client(cls, provider):
            cls.client = {"provider": provider}
            return cls.client, cls.PROVIDERS[provider]["model"]

    monkeypatch.setattr(cli, "A", FakeEngine)
    cli.cmd_models("custom")

    assert FakeEngine.PROVIDERS["custom"] == {
        "api_mode": "openai",
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "ornith-1.0-35b",
        "api_key": "sk-local",
    }
    assert FakeEngine.ACTIVE_PROVIDER == "custom"
    assert FakeEngine.MODEL_NAME == "ornith-1.0-35b"
    assert all("default" not in kwargs for kwargs in fake.text_kwargs)
    assert fake.secret_kwargs == [{"message": "API key:"}]


def test_models_custom_works_without_inquirerpy(monkeypatch):
    monkeypatch.setitem(sys.modules, "InquirerPy", None)
    inputs = iter(["http://127.0.0.1:8080/v1/chat/completions", "ornith-1.0-35b"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda _: "sk-local")

    from agent8088 import cli

    class FakeEngine:
        APP_CONFIG = {}
        PROVIDERS = {}
        ACTIVE_PROVIDER = ""
        MODEL_NAME = ""
        client = None

        @classmethod
        def get_client(cls, provider):
            cls.client = {"provider": provider}
            return cls.client, cls.PROVIDERS[provider]["model"]

    monkeypatch.setattr(cli, "A", FakeEngine)
    cli.cmd_models("custom")

    assert FakeEngine.PROVIDERS["custom"]["base_url"] == "http://127.0.0.1:8080/v1"
    assert FakeEngine.PROVIDERS["custom"]["api_key"] == "sk-local"
