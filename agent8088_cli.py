"""Compatibility shim for older imports of `agent8088_cli`."""
import sys

from agent8088 import cli as _cli

sys.modules[__name__] = _cli
