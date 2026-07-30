def test_load_providers_from_config(engine):
    cfg = {
        "provider.openai.base_url": "https://api.openai.com/v1",
        "provider.openai.model": "gpt-4o",
        "provider.openai.api_key": "sk-test",
        "provider.openrouter.base_url": "https://openrouter.ai/api/v1",
        "provider.openrouter.model": "anthropic/claude-3.5-sonnet",
        "provider.ornith.base_url": "http://127.0.0.1:8080/v1/chat/completions",
        "provider.ornith.model": "ornith-1.0-35b",
        "unrelated_key": "ignored",
    }
    provs = engine.load_providers(cfg)
    assert set(provs) == {"openai", "openrouter", "ornith"}
    assert provs["openai"]["model"] == "gpt-4o"
    assert provs["openrouter"]["base_url"].endswith("/api/v1")
    assert provs["ornith"]["base_url"] == "http://127.0.0.1:8080/v1"


def test_load_providers_requires_base_url(engine):
    # A provider with only a model and no endpoint is incomplete -> dropped.
    provs = engine.load_providers({"provider.broken.model": "x"})
    assert provs == {}


def test_load_providers_allows_litellm_without_base_url(engine):
    provs = engine.load_providers({
        "provider.claude.api_mode": "litellm",
        "provider.claude.model": "anthropic/claude-sonnet-4-5-20250929",
        "provider.claude.api_key_env": "ANTHROPIC_API_KEY",
    })
    assert provs["claude"]["api_mode"] == "litellm"


def test_get_client_for_named_provider(engine, monkeypatch):
    monkeypatch.setattr(engine, "PROVIDERS", {
        "acme": {"base_url": "https://acme.test/v1", "model": "acme-1", "api_key": "k"}})
    client, model = engine.get_client("acme")
    assert model == "acme-1"
    assert "acme.test" in str(client.base_url)


def test_configured_api_key_wins_over_adapter_environment_key(engine, monkeypatch):
    monkeypatch.setenv("ACME_API_KEY", "environment-key")

    assert engine._provider_api_key({
        "api_key": "configured-key",
        "api_key_env": "ACME_API_KEY",
    }) == "configured-key"


def test_all_builtin_adapters_accept_configured_api_keys(engine, monkeypatch):
    from agent8088.providers import BUILTIN_PROVIDERS

    config = {}
    for name in BUILTIN_PROVIDERS:
        config[f"provider.{name}.api_key"] = f"key-{name}"
        config[f"provider.{name}.model"] = f"model-{name}"
    monkeypatch.setattr(engine, "PROVIDERS", engine.load_providers(config, include_builtins=True))

    for name in BUILTIN_PROVIDERS:
        client, model = engine.get_client(name)
        assert client.api_key == f"key-{name}"
        assert model == f"model-{name}"


def test_openai_compatible_adapter_uses_active_model(engine, monkeypatch):
    calls = []
    response = type("Response", (), {"choices": []})()

    class Completions:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return response

    client = type("Client", (), {
        "chat": type("Chat", (), {"completions": Completions()})(),
    })()
    monkeypatch.setattr(engine, "MODEL_NAME", "adapter-model")

    assert engine.create_completion(
        client,
        [{"role": "user", "content": "hello"}],
        [],
    ) is response
    assert calls[0]["model"] == "adapter-model"
    assert calls[0]["messages"][-1] == {"role": "user", "content": "hello"}


def test_get_client_for_litellm_provider_uses_environment_key(engine, monkeypatch):
    monkeypatch.setattr(engine, "PROVIDERS", {
        "claude": {"api_mode": "litellm", "model": "anthropic/claude", "api_key_env": "ANTHROPIC_API_KEY"}})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client, model = engine.get_client("claude")
    assert client == {"api_mode": "litellm", "api_base": "", "api_key": "test-key"}
    assert model == "anthropic/claude"


def test_litellm_completion_uses_normalized_arguments(engine, monkeypatch):
    calls = []

    class FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            calls.append(kwargs)
            return "response"

    monkeypatch.setitem(__import__("sys").modules, "litellm", FakeLiteLLM)
    monkeypatch.setattr(engine, "MODEL_NAME", "anthropic/claude")
    assert engine.create_completion(
        {"api_mode": "litellm", "api_base": "", "api_key": "test-key"},
        [{"role": "user", "content": "hi"}], [],
    ) == "response"
    assert calls[0]["model"] == "anthropic/claude"
    assert calls[0]["api_key"] == "test-key"


def test_save_model_profile_never_writes_the_key(tmp_path):
    from agent8088.cli import save_model_profile

    path = tmp_path / "config.txt"
    save_model_profile(path, "claude", "litellm", "anthropic/claude", api_key_env="ANTHROPIC_API_KEY")
    saved = path.read_text()
    assert "provider.claude.api_key_env=ANTHROPIC_API_KEY" in saved
    assert "api_key=" not in saved


def test_get_client_unknown_provider_falls_back(engine):
    client, model = engine.get_client("does-not-exist")
    assert model  # falls back to the default model rather than crashing


def test_get_client_env_var_selects_provider(engine, monkeypatch):
    monkeypatch.setattr(engine, "PROVIDERS", {
        "envprov": {"base_url": "https://env.test/v1", "model": "env-1"}})
    monkeypatch.setenv("AGENT8088_PROVIDER", "envprov")
    _, model = engine.get_client()
    assert model == "env-1"


def test_activate_model_persists_new_default(engine, tmp_path, monkeypatch):
    config = tmp_path / "config.txt"
    config.write_text("allowed_paths=~\ndefault_provider=ollama\n", encoding="utf-8")
    monkeypatch.setattr(engine, "CONFIG_PATH", config)
    monkeypatch.setattr(engine, "APP_CONFIG", engine.load_simple_config(config))
    monkeypatch.setattr(engine, "PROVIDERS", {
        "custom": {
            "api_mode": "openai",
            "base_url": "http://127.0.0.1:8080/v1",
            "model": "old-model",
            "api_key": "sk-local",
        },
    })

    engine.activate_model("custom", "ornith-1.0-35b")

    restarted = engine.load_simple_config(config)
    assert restarted["default_provider"] == "custom"
    assert restarted["provider.custom.model"] == "ornith-1.0-35b"
    assert restarted["provider.custom.base_url"] == "http://127.0.0.1:8080/v1"
    assert restarted["provider.custom.api_key"] == "sk-local"
    assert engine.load_providers(restarted)["custom"]["model"] == "ornith-1.0-35b"


def test_provider_api_keys_are_redacted(engine, monkeypatch):
    # A provider key in config must be masked in any output, like the flat api_key.
    cfg = dict(engine.APP_CONFIG)
    cfg["provider.openai.api_key"] = "sk-supersecretvalue12345"
    secrets = engine.collect_secret_values(cfg)
    assert "sk-supersecretvalue12345" in secrets


def test_builtin_provider_catalog_skips_anthropic():
    from agent8088 import providers

    names = providers.builtin_provider_names()
    assert names == [
        "ollama", "openrouter", "openai", "gemini", "cerebras", "deepseek",
        "groq", "mistral", "moonshot", "qwen", "ollama-cloud", "copilot",
    ]
    assert "anthropic" not in names
    assert providers.builtin_provider_defaults("copilot")["default_model"] == "gpt-4o-mini"


def test_load_providers_can_seed_builtins(engine):
    provs = engine.load_providers({}, include_builtins=True)
    assert "copilot" in provs
    assert "anthropic" not in provs
    assert provs["openrouter"]["model"] == "anthropic/claude-sonnet-4"


def test_list_models_fetches_and_serves_cache(tmp_path, monkeypatch):
    from agent8088 import providers

    monkeypatch.setattr(providers, "_CACHE_FILE", tmp_path / "models_cache.json")

    class Model:
        def __init__(self, model_id):
            self.id = model_id

    class Models:
        def list(self):
            return type("Response", (), {"data": [Model("b"), Model("a")]})()

    class Client:
        base_url = "https://api.example/v1"
        api_key = "secret"
        models = Models()

    client = Client()
    assert providers.list_models("openai", client=None, fallback=False) == []
    assert providers.list_models("openai", client=client, fallback=False) == ["a", "b"]
    assert providers.list_models("openai", client=client, fallback=False) == ["a", "b"]


def test_model_cache_isolated_by_endpoint_and_account(tmp_path, monkeypatch):
    from agent8088 import providers

    monkeypatch.setattr(providers, "_CACHE_FILE", tmp_path / "models_cache.json")

    class Models:
        def __init__(self, model):
            self.model = model

        def list(self):
            item = type("Model", (), {"id": self.model})()
            return type("Response", (), {"data": [item]})()

    class Client:
        def __init__(self, endpoint, key, model):
            self.base_url = endpoint
            self.api_key = key
            self.models = Models(model)

    first = Client("https://one.example/v1", "key-one", "model-one")
    second = Client("https://two.example/v1", "key-two", "model-two")
    assert providers.list_models("custom", first, fallback=False) == ["model-one"]
    assert providers.list_models("custom", second, fallback=False) == ["model-two"]
