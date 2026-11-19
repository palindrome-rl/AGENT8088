"""Malformed server headers must not replace the original provider failure."""
from types import SimpleNamespace

import pytest

from agent8088 import engine, errors


@pytest.mark.parametrize("raw", ["inf", "Infinity", "-inf", "NaN", "1e999"])
def test_nonfinite_retry_after_is_ignored(raw):
    error = RuntimeError("rate limited")
    error.response = SimpleNamespace(headers={"retry-after": raw})
    assert errors.retry_after_seconds(error) is None
    assert engine._extract_retry_after(error) is None
