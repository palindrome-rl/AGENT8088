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
