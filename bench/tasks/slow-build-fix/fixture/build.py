"""Build the app from src/modules into out/.

    python build.py

Each module is compiled in turn (this is slow, about three minutes in all),
then the link step checks that every symbol a module uses is defined by some
module. On success it writes out/app.bin and out/manifest.json.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPILE_SECONDS = 0.0 if os.environ.get("BUILD_FAST") == "1" else 1.5


def parse(path):
    defines, uses = [], []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, _, name = line.partition(" ")
        if kind == "def" and name:
            defines.append(name)
        elif kind == "use" and name:
            uses.append(name)
        else:
            raise SystemExit(f"{path.name}:{number}: cannot read '{line}'")
    return defines, uses


def main():
    modules = sorted((ROOT / "src" / "modules").glob("*.txt"))
    if not modules:
        raise SystemExit("no modules in src/modules")
    compiled = {}
    for index, path in enumerate(modules, 1):
        compiled[path.stem] = parse(path)
        time.sleep(COMPILE_SECONDS)
        print(f"[{index}/{len(modules)}] compiled {path.stem}", flush=True)
    defined = {}
    for name, (defines, _) in compiled.items():
        for symbol in defines:
            if symbol in defined:
                raise SystemExit(f"link error: {symbol} is defined in both {defined[symbol]} and {name}")
            defined[symbol] = name
    missing = [(name, symbol) for name, (_, uses) in compiled.items() for symbol in uses if symbol not in defined]
    if missing:
        for name, symbol in missing:
            print(f"link error: {name} uses {symbol}, which no module defines", flush=True)
        raise SystemExit(1)
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    blob = json.dumps({name: compiled[name] for name in sorted(compiled)}, sort_keys=True).encode("utf-8")
    (out / "app.bin").write_bytes(blob)
    manifest = {"modules": len(compiled), "symbols": len(defined),
                "links": sum(len(uses) for _, uses in compiled.values()),
                "sha256": hashlib.sha256(blob).hexdigest()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"build ok: {manifest['modules']} modules, {manifest['symbols']} symbols", flush=True)


if __name__ == "__main__":
    sys.exit(main())
