"""Whether a model reads images is decided from what is declared, published or observed, never from its name.

Order: a `provider.<name>.vision` (or flat `model_vision`) setting, then the model catalog and what
the endpoint publishes, then one live probe: a small picture with a known answer. A model that
answers it correctly reads images; one that cannot does not. The known answer is what catches a
server that silently drops images. The probe runs only when an image is about to be sent, once per
endpoint and model, and its answer is cached on disk.

A request that the server rejects because the model takes no images marks the model text-only and
is resent without image parts, once.
"""
import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from agent8088 import model_catalog, providers


# --- fake endpoints ------------------------------------------------------------------------------------

NAMES = {(220, 30, 30): "red", (30, 160, 60): "green", (40, 80, 220): "blue",
         (240, 200, 20): "yellow", (20, 20, 20): "black", (245, 245, 245): "white"}


def _colours_in(messages):
    """What a model that can see would answer: the colours of the probe picture, left then right."""
    for message in messages:
        for part in message.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "image_url":
                raw = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
                with Image.open(io.BytesIO(raw)) as img:
                    img = img.convert("RGB")
                    w, h = img.size
                    left, right = img.getpixel((w // 4, h // 2)), img.getpixel((3 * w // 4, h // 2))
                return NAMES[left], NAMES[right]
    raise AssertionError("no image in the probe")


class _Client:
    def __init__(self, behaviour, base_url="http://box:8000/v1", api_key="k1"):
        self.behaviour, self.base_url, self.api_key, self.calls = behaviour, base_url, api_key, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        if self.behaviour == "sees":
            left, right = _colours_in(kwargs["messages"])
            text = f"{left.capitalize()} and {right}."
        elif self.behaviour == "blind":
            text = "I'm not able to view images, but it might be red and blue."
        elif self.behaviour == "rejects":
            raise RuntimeError("Error code: 400 - this model does not support image input")
        else:
            raise ConnectionError("connection refused")
        message = SimpleNamespace(content=text, reasoning_content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def cache(monkeypatch, tmp_path):
    store = {}
    monkeypatch.setattr(providers, "_load_disk_cache", lambda: dict(store))
    monkeypatch.setattr(providers, "_save_disk_cache", lambda d: (store.clear(), store.update(d)))
    return store


# --- the live probe -------------------------------------------------------------------------------------

def test_a_model_that_sees_the_picture_passes_the_probe(cache):
    assert providers.probe_image_understanding(_Client("sees"), "custom-vl", "box") is True


def test_a_model_that_does_not_see_it_fails_the_probe(cache):
    assert providers.probe_image_understanding(_Client("blind"), "custom-text", "box") is False


def test_a_server_that_rejects_images_fails_the_probe(cache):
    assert providers.probe_image_understanding(_Client("rejects"), "custom-text", "box") is False


def test_an_unreachable_server_gives_no_answer_and_nothing_is_cached(cache):
    assert providers.probe_image_understanding(_Client("down"), "custom-vl", "box") is None
    assert not cache


def test_the_answer_is_cached_per_endpoint_and_model(cache):
    client = _Client("sees")
    assert providers.probe_image_understanding(client, "custom-vl", "box") is True
    assert providers.probe_image_understanding(client, "custom-vl", "box") is True
    assert client.calls == 1
    other = _Client("sees", base_url="http://other:8000/v1")
    providers.probe_image_understanding(other, "custom-vl", "box")
    assert other.calls == 1, "another endpoint is asked separately"


def test_a_forced_probe_asks_again(cache):
    client = _Client("sees")
    providers.probe_image_understanding(client, "custom-vl", "box")
    providers.probe_image_understanding(client, "custom-vl", "box", force=True)
    assert client.calls == 2


# --- the decision ---------------------------------------------------------------------------------------

@pytest.fixture
def unknown_model(engine, monkeypatch, cache):
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "box")
    monkeypatch.setattr(engine, "MODEL_NAME", "brand-new-vl")
    monkeypatch.setattr(model_catalog, "vision_decision",
                        lambda provider, model, client=None: model_catalog.VisionDecision(False, "default"))
    engine._model_vision_cache.clear()
    return engine


def test_a_declared_setting_wins_over_everything(unknown_model, monkeypatch):
    engine = unknown_model
    monkeypatch.setattr(engine, "client", _Client("blind"))
    monkeypatch.setitem(engine.APP_CONFIG, "provider.box.vision", "1")
    decision = engine.model_vision_decision()
    assert decision.vision is True and decision.source == "config"
    assert engine.client.calls == 0, "no probe when the user said so"
    monkeypatch.setitem(engine.APP_CONFIG, "provider.box.vision", "0")
    monkeypatch.setattr(model_catalog, "vision_decision",
                        lambda provider, model, client=None: model_catalog.VisionDecision(True, "bundled"))
    assert engine.model_vision_decision().vision is False


def test_the_flat_setting_applies_without_a_named_provider(unknown_model, monkeypatch):
    engine = unknown_model
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "")
    monkeypatch.setitem(engine.APP_CONFIG, "model_vision", "yes")
    assert engine.model_vision_decision().source == "config"


def test_an_unknown_model_is_probed_once_when_an_image_is_needed(unknown_model, monkeypatch):
    engine = unknown_model
    monkeypatch.setattr(engine, "client", _Client("sees"))
    assert engine._model_accepts_images() is True
    assert engine._model_accepts_images() is True
    assert engine.client.calls == 1
    assert engine.model_vision_decision().source == "live probe"


def test_a_blind_unknown_model_stays_text_only(unknown_model, monkeypatch):
    engine = unknown_model
    monkeypatch.setattr(engine, "client", _Client("blind"))
    assert engine._model_accepts_images() is False


def test_the_offline_decision_never_probes(unknown_model, monkeypatch):
    engine = unknown_model
    monkeypatch.setattr(engine, "client", _Client("sees"))
    assert engine.model_vision_decision(probe=False).source == "default"
    assert engine.client.calls == 0


def test_the_cli_image_path_uses_the_same_decision(unknown_model, monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "client", _Client("sees"))
    monkeypatch.setattr(cli, "_active_provider_name", lambda: "box")
    assert cli._vision_capable() is True


# --- learning from a rejection --------------------------------------------------------------------------

def test_a_rejected_image_marks_the_model_text_only_and_resends_without_it(unknown_model, monkeypatch, tmp_path):
    engine = unknown_model
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "BLOCKED_PATHS", [])
    Image.new("RGB", (40, 40), (220, 30, 30)).save(tmp_path / "house.png")
    spec = engine._build_spec("view_image", {"mode": "image", "args": "path", "path_arg": "path"},
                              engine.APP_CONFIG, "Attach an image.")
    monkeypatch.setitem(engine.TOOL_SPECS, "view_image", spec)
    engine._model_vision_cache[engine._model_key()] = True      # wrongly believed to read images
    replies = ['✿FUNCTION✿: view_image ✿ARGS✿: {"path": "house.png"}', "Done without the picture."]
    sent = []

    def completion(messages, *_a, **_k):
        images = sum(1 for m in messages if isinstance(m.get("content"), list)
                     for p in m["content"] if p.get("type") == "image_url")
        sent.append(images)
        if images:
            raise RuntimeError("Error code: 400 - {'message': 'Model does not support image input'}")
        text = replies[min(len([s for s in sent if s == 0]) - 1, 1)]
        message = type("Message", (), {"content": text})()
        return type("Response", (), {"choices": [type("Choice", (), {"message": message, "finish_reason": "stop"})()]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": "Look at house.png"}], max_turns=6, tools_def=[],
                              allowed_tools={"view_image"}, system_prompt="", memory_capture=False, trace=trace)
    assert answer.startswith("Done without the picture."), answer
    assert sent.count(1) == 1 and sent[-1] == 0
    assert any(step["type"] == "image_input_unsupported" for step in trace)
    assert engine._model_accepts_images() is False


# --- telling the user -----------------------------------------------------------------------------------

def test_the_withheld_note_says_how_to_turn_images_on(unknown_model, monkeypatch):
    engine = unknown_model
    message = {"role": "user", "content": [{"type": "text", "text": "Tool result (view_image): a.png"}]}
    note = engine._text_only_turn(message)["content"]
    assert "provider.box.vision=1" in note


def test_capabilities_report_image_input_and_its_source(unknown_model, monkeypatch):
    engine = unknown_model
    monkeypatch.setitem(engine.APP_CONFIG, "provider.box.vision", "1")
    assert "Image input: yes (config)" in engine.describe_capabilities()


def test_the_vision_command_reports_and_reprobes(unknown_model, monkeypatch):
    from agent8088 import cli
    shown = []
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: shown.extend(str(x) for x in a))
    client = _Client("sees")
    monkeypatch.setattr(cli.A, "client", client)
    cli.cmd_vision("")
    assert any("image input" in line.lower() for line in shown)
    cli.cmd_vision("probe")
    cli.cmd_vision("probe")
    assert client.calls == 2, "probe always asks again"
    assert "vision" in cli.COMMANDS


def test_a_cached_probe_answer_is_reported_offline_in_a_new_session(unknown_model, monkeypatch):
    """The offline decision (status lines, the web upload chip) reads the probe cache without calling anything."""
    engine = unknown_model
    first = _Client("sees")
    monkeypatch.setattr(engine, "client", first)
    assert engine._model_accepts_images() is True
    engine._model_vision_cache.clear()                     # a new session, same endpoint and model
    again = _Client("sees")
    monkeypatch.setattr(engine, "client", again)
    decision = engine.model_vision_decision(probe=False)
    assert decision.vision is True and decision.source == "live probe"
    assert again.calls == 0
