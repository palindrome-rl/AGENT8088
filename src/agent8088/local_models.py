"""Local hardware probing + Ollama model lifecycle management.

Agent8088 already talks to a local Ollama daemon as the "ollama" provider
(providers.py: http://localhost:11434/v1) -- Ollama itself already downloads,
serves, and manages VRAM/RAM for local models. This module does NOT
reimplement a model runtime the way e.g. hermes-agent's own llama.cpp
binary/process supervisor does; it only adds what Ollama doesn't already
answer: (1) a hardware probe, (2) a size-based fit recommendation against a
small curated model list, and (3) thin wrappers around Ollama's own REST API
for pull/list/remove.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

_GIB = 1024 ** 3
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$")


class OllamaError(Exception):
    """Raised when the local Ollama daemon can't be reached or errors out."""


@dataclass
class HardwareBudget:
    ram_total_gb: float
    ram_free_gb: float
    gpu_name: str | None
    vram_total_gb: float | None
    vram_free_gb: float | None
    source: str  # "nvidia-smi" or "ram-only"
    caveat: str = ""


def _probe_gpu() -> tuple[str | None, float | None, float | None, str]:
    """NVIDIA only, via nvidia-smi (no other vendor's tooling is assumed
    present). Returns (name, total_gb, free_gb, caveat); all None/"" when no
    GPU is found or nvidia-smi isn't on PATH."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None, None, None, ""
    try:
        out = subprocess.run(
            [smi, "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, None, None, ""
    line = out.splitlines()[0] if out else ""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) != 3:
        return None, None, None, ""
    name, total_mib, free_mib = parts
    try:
        total_gb = float(total_mib) / 1024
        free_gb = float(free_mib) / 1024
    except ValueError:
        return None, None, None, ""
    caveat = ("nvidia-smi's free/total figures can be off on some "
              "unified-memory or WDDM setups -- treat this as an estimate.")
    return name, total_gb, free_gb, caveat


def probe_hardware() -> HardwareBudget:
    import psutil  # imported here so a missing/broken psutil only breaks this call

    vm = psutil.virtual_memory()
    gpu_name, vram_total, vram_free, caveat = _probe_gpu()
    return HardwareBudget(
        ram_total_gb=round(vm.total / _GIB, 1),
        ram_free_gb=round(vm.available / _GIB, 1),
        gpu_name=gpu_name,
        vram_total_gb=round(vram_total, 1) if vram_total is not None else None,
        vram_free_gb=round(vram_free, 1) if vram_free is not None else None,
        source="nvidia-smi" if gpu_name else "ram-only",
        caveat=caveat,
    )


# name -> (approx params in billions, approx download size in GB at the
# quant Ollama ships by default -- mostly Q4_K_M). Rough figures for a fit
# *recommendation*; Ollama's own daemon is the authority on whether a pull
# actually fits at pull/load time.
_MODEL_CATALOG = [
    ("llama3.2:1b", 1, 1.3),
    ("llama3.2:3b", 3, 2.0),
    ("qwen2.5-coder:7b", 7, 4.7),
    ("llama3.1:8b", 8, 4.9),
    ("gemma2:9b", 9, 5.4),
    ("qwen2.5-coder:14b", 14, 9.0),
    ("qwen2.5-coder:32b", 32, 20.0),
    ("llama3.3:70b", 70, 43.0),
    ("gpt-oss:120b", 120, 65.0),
]

# Rough runtime overhead (KV cache, framework buffers) on top of raw weight
# size before a model is comfortable, not just loadable.
_HEADROOM_FACTOR = 1.3


def recommend_models(hw: HardwareBudget) -> list[str]:
    """Rank _MODEL_CATALOG against the probed budget. A GPU budgets from
    VRAM (fast); no GPU falls back to free RAM (CPU inference -- works, but
    slow)."""
    if hw.vram_total_gb:
        budget_gb = hw.vram_total_gb
        mode = "GPU"
    else:
        budget_gb = hw.ram_free_gb
        mode = "CPU, no GPU detected -- will be slow"
    lines = []
    for name, params_b, size_gb in _MODEL_CATALOG:
        if size_gb * _HEADROOM_FACTOR <= budget_gb:
            lines.append(f"{name} ({params_b}B, ~{size_gb:.1f} GB) -- fits comfortably [{mode}]")
        elif size_gb <= budget_gb:
            lines.append(f"{name} ({params_b}B, ~{size_gb:.1f} GB) -- fits tightly, little headroom [{mode}]")
    if not lines:
        smallest = _MODEL_CATALOG[0]
        lines.append(
            f"Even {smallest[0]} (~{smallest[2]:.1f} GB) may not fit comfortably -- "
            f"only ~{budget_gb:.1f} GB available."
        )
    return lines


def format_hardware_report(hw: HardwareBudget) -> str:
    lines = [f"RAM: {hw.ram_free_gb:.1f} GB free / {hw.ram_total_gb:.1f} GB total"]
    if hw.gpu_name:
        lines.append(
            f"GPU: {hw.gpu_name} -- {hw.vram_free_gb:.1f} GB free / "
            f"{hw.vram_total_gb:.1f} GB total VRAM"
        )
        if hw.caveat:
            lines.append(f"Note: {hw.caveat}")
    else:
        lines.append("GPU: none detected (NVIDIA only, via nvidia-smi) -- local models run on CPU")
    lines.append("")
    lines.append("Recommended models for this machine:")
    for rec in recommend_models(hw):
        lines.append(f"  - {rec}")
    return "\n".join(lines)


# --- Ollama lifecycle wrappers, over Ollama's own REST API ---------------

def _ollama_request(path: str, method: str = "GET", body: dict | None = None,
                     host: str = DEFAULT_OLLAMA_HOST, timeout: int = 15) -> dict:
    url = f"{host.rstrip('/')}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"Can't reach Ollama at {host} -- is 'ollama serve' running? ({exc})"
        ) from exc
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Ollama returned unparseable output: {exc}") from exc


def list_installed_models(host: str = DEFAULT_OLLAMA_HOST) -> list[dict]:
    return _ollama_request("/api/tags", host=host).get("models", [])


def running_models(host: str = DEFAULT_OLLAMA_HOST) -> list[dict]:
    return _ollama_request("/api/ps", host=host).get("models", [])


def _validate_model_name(name: str) -> str:
    name = (name or "").strip()
    if not name or not _MODEL_NAME_RE.match(name):
        raise OllamaError(f"Invalid model name: {name!r}")
    return name


def pull_model(name: str, host: str = DEFAULT_OLLAMA_HOST, timeout: int = 600) -> str:
    """Streams NDJSON progress from /api/pull, returns the last status line.
    A large model can take longer than the default tool timeout on a slow
    connection -- raise max_tool_timeout_seconds in config.txt if pulls
    keep timing out partway."""
    name = _validate_model_name(name)
    url = f"{host.rstrip('/')}/api/pull"
    req = urllib.request.Request(
        url, data=json.dumps({"model": name}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    last_status = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                if not raw_line.strip():
                    continue
                try:
                    evt = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if evt.get("error"):
                    raise OllamaError(evt["error"])
                last_status = evt.get("status") or last_status
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"Can't reach Ollama at {host} -- is 'ollama serve' running? ({exc})"
        ) from exc
    return last_status or "pull finished"


def remove_model(name: str, host: str = DEFAULT_OLLAMA_HOST) -> str:
    name = _validate_model_name(name)
    _ollama_request("/api/delete", method="DELETE", body={"model": name}, host=host)
    return f"Removed {name}"
