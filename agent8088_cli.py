"""Compatibility shim for older imports of `agent8088_cli`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from agent8088 import cli as _cli

if __name__ == "__main__":
    _cli.main()
else:
    sys.modules[__name__] = _cli
