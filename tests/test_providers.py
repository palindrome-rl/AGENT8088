def test_load_providers_from_config(engine):
    cfg = {
        "provider.openai.base_url": "https://api.openai.com/v1",
        "provider.openai.model": "gpt-4o",
        "provider.openai.api_key": "sk-test",
        "provider.openrouter.base_url": "https://openrouter.ai/api/v1",
        "provider.openrouter.model": "anthropic/claude-3.5-sonnet",
        "unrelated_key": "ignored",
    }
    provs = engine.load_providers(cfg)
    assert set(provs) == {"openai", "openrouter"}
    assert provs["openai"]["model"] == "gpt-4o"
    assert provs["openrouter"]["base_url"].endswith("/api/v1")


def test_load_providers_requires_base_url(engine):
    # A provider with only a model and no endpoint is incomplete -> dropped.
    provs = engine.load_providers({"provider.broken.model": "x"})
    assert provs == {}


def test_get_client_for_named_provider(engine, monkeypatch):
    monkeypatch.setattr(engine, "PROVIDERS", {
        "acme": {"base_url": "https://acme.test/v1", "model": "acme-1", "api_key": "k"}})
    client, model = engine.get_client("acme")
    assert model == "acme-1"
    assert "acme.test" in str(client.base_url)


def test_get_client_unknown_provider_falls_back(engine):
    client, model = engine.get_client("does-not-exist")
    assert model  # falls back to the default model rather than crashing


def test_get_client_env_var_selects_provider(engine, monkeypatch):
    monkeypatch.setattr(engine, "PROVIDERS", {
        "envprov": {"base_url": "https://env.test/v1", "model": "env-1"}})
    monkeypatch.setenv("AGENT8088_PROVIDER", "envprov")
    _, model = engine.get_client()
    assert model == "env-1"


def test_provider_api_keys_are_redacted(engine, monkeypatch):
    # A provider key in config must be masked in any output, like the flat api_key.
    cfg = dict(engine.APP_CONFIG)
    cfg["provider.openai.api_key"] = "sk-supersecretvalue12345"
    secrets = engine.collect_secret_values(cfg)
    assert "sk-supersecretvalue12345" in secrets
