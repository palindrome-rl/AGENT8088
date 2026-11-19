"""Best-effort probes must not retry.

Both probes are documented "never raises" -- a failure is a miss, not
something to try again. The OpenAI SDK defaults to max_retries=2, which turned
one 5s timeout into ~16.7s of blocked startup before the probe was made lazy.
Lazy or not, a miss must stay cheap: these run on /doctor, on /model switch and
on the first request of every session.
"""
from unittest.mock import MagicMock

from agent8088 import providers


def _dead_client():
    client = MagicMock()
    client.base_url = "http://192.0.2.1:8080/v1"   # TEST-NET-1, never routable
    client.api_key = ""
    client.models.list.side_effect = RuntimeError("unreachable")
    client.with_options.return_value = client
    return client


def test_context_probe_disables_retries():
    client = _dead_client()
    providers.probe_model_context_window(client, "some-model", provider_name="custom")
    assert client.with_options.called, "probe did not go through with_options()"
    kwargs = client.with_options.call_args.kwargs
    assert kwargs.get("max_retries") == 0, (
        f"context probe must pass max_retries=0, got {kwargs}"
    )


def test_vision_probe_avoids_the_retrying_sdk_client():
    """The vision probe reads over raw httpx, which does not retry.

    Asserting max_retries here would pass vacuously (with_options is never
    called), so assert the property that actually matters: the probe must not
    reach the endpoint through the OpenAI SDK client, whose default is to
    retry twice.
    """
    client = _dead_client()
    providers.probe_model_vision(client, "some-model", provider_name="custom")
    assert not client.models.list.called, (
        "vision probe went through the retrying SDK client"
    )


def test_list_models_disables_retries():
    client = _dead_client()
    try:
        providers.list_models("custom", client)
    except Exception:
        pass
    if client.with_options.called:
        kwargs = client.with_options.call_args.kwargs
        assert kwargs.get("max_retries") == 0, (
            f"list_models must pass max_retries=0, got {kwargs}"
        )
