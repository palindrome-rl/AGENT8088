from agent8088.gateway.session import SessionStore, build_session_key


def test_build_session_key_private():
    assert build_session_key("slack", "private", "U123") == "agent:main:slack:private:U123"


def test_build_session_key_channel_with_thread():
    assert build_session_key("slack", "channel", "C456", thread_id="1700000.0") == "agent:main:slack:channel:C456:1700000.0"


def test_session_store_save_load_roundtrip(tmp_path):
    store = SessionStore(base_dir=str(tmp_path))
    store.save("agent:main:slack:private:U1", [{"role": "user", "content": "hi"}])
    assert store.load("agent:main:slack:private:U1") == [{"role": "user", "content": "hi"}]


def test_session_store_load_missing_returns_empty(tmp_path):
    store = SessionStore(base_dir=str(tmp_path))
    assert store.load("nonexistent") == []


def test_session_store_clear(tmp_path):
    store = SessionStore(base_dir=str(tmp_path))
    store.save("k1", [{"role": "user", "content": "y"}])
    store.clear("k1")
    assert store.load("k1") == []


def test_session_store_list_all(tmp_path):
    store = SessionStore(base_dir=str(tmp_path))
    store.save("k1", [{"role": "user", "content": "a"}])
    store.save("k2", [{"role": "user", "content": "b"}])
    keys = set(store.list_all())
    assert keys == {"k1", "k2"}