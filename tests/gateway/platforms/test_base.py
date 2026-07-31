import pytest
from agent8088.gateway.platforms.base import (
    MessageEvent, SendResult, BaseChannelAdapter, StreamSink,
)


def test_message_event_defaults():
    evt = MessageEvent(platform="slack", chat_id="C1", chat_type="channel",
                       user_id="U1", text="hi")
    assert evt.attachments == []
    assert evt.thread_id is None
    assert evt.raw is None


def test_send_result_defaults():
    r = SendResult()
    assert r.ok is True
    assert r.message_id is None
    assert r.error is None


def test_base_adapter_is_abstract():
    with pytest.raises(TypeError):
        BaseChannelAdapter()


def test_stream_sink_protocol_shape():
    class _Sink:
        def __call__(self, delta): pass
        def finalize(self, full): pass
        def fail(self, err): pass
    assert isinstance(_Sink(), StreamSink)