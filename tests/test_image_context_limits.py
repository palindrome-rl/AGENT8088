"""Images a tool attaches are sized for the model, capped in number, and never break a text-only model.

A tool in `mode=image` returns a marker that the agent loop swaps for a multimodal message. Three
things keep that safe on a small local model:

* the copy sent to the model is downscaled (longest side and byte cap from config), while the file on
  disk stays full size;
* only the most recent images stay in the conversation; older ones become a short text note, so a
  long session cannot fill the context with pictures;
* a model without image input gets the tool's text and a plain note instead of image parts.

The fallback-model token budget also counts an image as a flat allowance, not its base64 length.
"""
import base64
import io
import json

import pytest
from PIL import Image


def _png(path, size=(1600, 1000), colour=(200, 40, 40)):
    Image.new("RGB", size, colour).save(path, "PNG")
    return path


def _data_url_size(message):
    """(width, height, bytes) of the first image part of a multimodal message."""
    for part in message["content"]:
        if part.get("type") == "image_url":
            url = part["image_url"]["url"]
            raw = base64.b64decode(url.split(",", 1)[1])
            with Image.open(io.BytesIO(raw)) as img:
                return img.size[0], img.size[1], len(raw)
    raise AssertionError("no image part")


@pytest.fixture
def workspace(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "BLOCKED_PATHS", [])
    spec = engine._build_spec("view_image", {"mode": "image", "args": "path", "path_arg": "path",
                                             "timeout": "30"}, engine.APP_CONFIG,
                              "Attach an image file to your next message.")
    monkeypatch.setitem(engine.TOOL_SPECS, "view_image", spec)
    return tmp_path


# --- the model copy is downscaled ----------------------------------------------------------------------

def test_a_tool_image_is_downscaled_for_the_model(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "MODEL_IMAGE_MAX_SIDE", 512)
    picture = _png(workspace / "house.png", (2000, 1200))
    message = engine.build_image_message("attached", [str(picture)], for_model=True)
    width, height, _ = _data_url_size(message)
    assert max(width, height) == 512
    assert round(width / height, 2) == round(2000 / 1200, 2), "aspect ratio is kept"
    with Image.open(picture) as on_disk:
        assert on_disk.size == (2000, 1200), "the file itself is untouched"


def test_a_small_image_is_sent_as_it_is(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "MODEL_IMAGE_MAX_SIDE", 768)
    picture = _png(workspace / "logo.png", (300, 200))
    width, height, size = _data_url_size(engine.build_image_message("attached", [str(picture)], for_model=True))
    assert (width, height) == (300, 200)
    assert size == picture.stat().st_size


def test_the_model_copy_respects_the_byte_cap(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "MODEL_IMAGE_MAX_SIDE", 1600)
    monkeypatch.setattr(engine, "MODEL_IMAGE_MAX_BYTES", 60_000)
    noisy = Image.effect_noise((1600, 1600), 100).convert("RGB")
    noisy.save(workspace / "chart.png", "PNG")
    _width, _height, size = _data_url_size(
        engine.build_image_message("attached", [str(workspace / "chart.png")], for_model=True))
    assert size <= 60_000


def test_a_user_pasted_image_is_not_downscaled(engine, workspace, monkeypatch):
    """Only tool-produced copies are resized; /image keeps its existing behaviour."""
    monkeypatch.setattr(engine, "MODEL_IMAGE_MAX_SIDE", 256)
    picture = _png(workspace / "photo.png", (900, 600))
    width, height, _ = _data_url_size(engine.build_image_message("pasted", [str(picture)]))
    assert (width, height) == (900, 600)


def test_the_image_tool_sends_the_downscaled_copy(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "MODEL_IMAGE_MAX_SIDE", 400)
    _png(workspace / "layout.png", (1200, 900))
    result = engine.run_tool("view_image", {"path": "layout.png"})
    assert result.startswith(engine._IMAGE_MARKER_PREFIX)
    message = json.loads(result[len(engine._IMAGE_MARKER_PREFIX):])["message"]
    width, height, _ = _data_url_size(message)
    assert max(width, height) == 400


# --- only the most recent images stay in context -------------------------------------------------------

def _image_turn(name):
    return {"role": "user", "content": [
        {"type": "text", "text": f"Tool result (view_image): {name} is attached."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}


def test_only_the_most_recent_images_are_kept(engine):
    messages = [{"role": "user", "content": "build a page"}]
    for i in range(5):
        messages.append({"role": "assistant", "content": f"render {i}"})
        messages.append(_image_turn(f"shot{i}.png"))
    removed = engine._prune_old_images(messages, keep=2)
    assert removed == 3
    with_images = [m for m in messages if isinstance(m["content"], list)
                   and any(p.get("type") == "image_url" for p in m["content"])]
    assert [engine._message_text(m).strip() for m in with_images] == [
        "Tool result (view_image): shot3.png is attached.", "Tool result (view_image): shot4.png is attached."]
    older = engine._message_text(messages[2])
    assert "shot0.png" in older, "the text that came with an image is kept"
    assert "removed from the conversation" in older


def test_pruning_with_room_to_spare_changes_nothing(engine):
    messages = [_image_turn("a.png"), _image_turn("b.png")]
    before = json.dumps(messages)
    assert engine._prune_old_images(messages, keep=3) == 0
    assert json.dumps(messages) == before


def test_keep_zero_means_no_limit(engine):
    messages = [_image_turn(f"{i}.png") for i in range(6)]
    assert engine._prune_old_images(messages, keep=0) == 0


# --- the agent loop -------------------------------------------------------------------------------------

def _completion(replies, seen):
    def completion(messages, *_args, **_kwargs):
        seen.append([json.loads(json.dumps(m)) for m in messages])
        text = replies[min(len(seen) - 1, len(replies) - 1)]
        message = type("Message", (), {"content": text})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()
    return completion


CALL = '✿FUNCTION✿: view_image ✿ARGS✿: {{"path": "{name}"}}'


def _run(engine, monkeypatch, replies, vision=True):
    seen = []
    monkeypatch.setattr(engine, "_create_completion_with_fallback", _completion(replies, seen))
    monkeypatch.setattr(engine, "_model_accepts_images", lambda: vision)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": "Look at the pictures."}], max_turns=12,
                              tools_def=[], allowed_tools={"view_image"}, system_prompt="",
                              memory_capture=False, trace=trace)
    return answer, seen, trace


def _images_in(messages):
    return sum(1 for m in messages if isinstance(m.get("content"), list)
               for p in m["content"] if p.get("type") == "image_url")


def test_the_loop_keeps_at_most_the_configured_number_of_images(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "MODEL_IMAGE_KEEP", 2)
    for i in range(4):
        _png(workspace / f"shot{i}.png", (64, 64), (i * 40, 80, 120))
    replies = [CALL.format(name=f"shot{i}.png") for i in range(4)] + ["All four looked fine."]
    answer, seen, trace = _run(engine, monkeypatch, replies)
    assert [step["type"] for step in trace].count("image_attached") == 4
    assert max(_images_in(call) for call in seen) <= 2
    assert _images_in(seen[-1]) == 2


def test_a_text_only_model_gets_the_tool_text_and_a_note(engine, workspace, monkeypatch):
    _png(workspace / "lighthouse.png", (64, 64))
    answer, seen, trace = _run(engine, monkeypatch, [CALL.format(name="lighthouse.png"), "Understood."],
                               vision=False)
    assert answer.startswith("Understood.")
    last = seen[-1]
    assert _images_in(last) == 0, "no image parts reach a text-only model"
    note = " ".join(engine._message_text(m) for m in last)
    assert "lighthouse.png" in note
    assert "this model has no image input" in note
    assert any(step["type"] == "image_withheld" for step in trace)


# --- the fallback budget counts an image as an allowance, not its base64 --------------------------------

def test_the_fallback_budget_is_not_swamped_by_image_data(engine, monkeypatch):
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *_a: (32_768, 8_192))
    huge = "data:image/png;base64," + "A" * 1_500_000
    messages = [{"role": "user", "content": [{"type": "text", "text": "look"},
                                             {"type": "image_url", "image_url": {"url": huge}}]}]
    assert engine._fallback_max_tokens(messages, "", [], "p", "m") > 4_000


# --- a server that takes fewer images per request ----------------------------------------------------------

class _ImageLimitError(Exception):
    """What vLLM answers when a request carries more images than --limit-mm-per-prompt allows."""


def _limited_completion(limit, replies, seen):
    def completion(messages, *_args, **_kwargs):
        count = _images_in(messages)
        seen.append(count)
        if count > limit:
            raise _ImageLimitError(f"Error code: 400 - {{'error': {{'message': 'At most {limit} image(s) may be "
                                   "provided in one prompt. (parameter=image)', 'code': 400}}")
        text = replies[min(sum(1 for c in seen if c <= limit) - 1, len(replies) - 1)]
        message = type("Message", (), {"content": text})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()
    return completion


def test_a_server_image_limit_is_learned_and_the_call_retried(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "MODEL_IMAGE_KEEP", 3)
    engine._model_image_limits.clear()
    for i in range(3):
        _png(workspace / f"shot{i}.png", (64, 64), (i * 60, 90, 120))
    seen = []
    replies = [CALL.format(name=f"shot{i}.png") for i in range(3)] + ["All three looked fine."]
    monkeypatch.setattr(engine, "_create_completion_with_fallback", _limited_completion(1, replies, seen))
    monkeypatch.setattr(engine, "_model_accepts_images", lambda: True)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": "Look at the pictures."}], max_turns=12,
                              tools_def=[], allowed_tools={"view_image"}, system_prompt="",
                              memory_capture=False, trace=trace)
    assert answer.startswith("All three looked fine."), answer
    assert seen.count(2) == 1, "the server refuses once, then the limit is known"
    assert max(seen[seen.index(2) + 1:]) <= 1
    assert any(step["type"] == "image_limit_learned" and step["limit"] == 1 for step in trace)
    assert engine._image_keep() == 1


def test_a_failed_turn_never_prints_raw_image_data(engine):
    message = {"role": "user", "content": [
        {"type": "text", "text": "Tool result (screenshot_page): page.html rendered. Page errors: none"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "QUJD" * 500}}]}
    marker = engine._IMAGE_MARKER_PREFIX + json.dumps({"message": message})
    answer = engine._fallback_answer(marker, RuntimeError("backend down"))
    assert "base64" not in answer and "QUJD" not in answer and "IMAGE" not in answer.split("\n\n", 1)[-1][:10]
    assert "Page errors: none" in answer
    assert "image is not shown" in answer
