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
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

_GIB = 1024 ** 3
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$")


class OllamaError(Exception):
    """Raised when the local Ollama daemon can't be reached or errors out."""


def _daemon_unreachable_message(host: str, exc: Exception) -> str:
    # Explicit "do not try to fix this yourself" instruction: a model that
    # hits a plain "is ollama serve running?" message will reach for
    # execute_shell to start the daemon itself, poll it, hit the shell
    # tool's loopback/SSRF guard on its own curl check, and burn its whole
    # turn budget on a diagnostic loop that never resolves the turn --
    # observed live: 10 turns spent trying `start ollama serve`,
    # `tasklist`, and a blocked `curl localhost` before hitting the turn
    # limit, even though the daemon was reachable moments later. Starting
    # a background service the agent doesn't own and can't verify healthy
    # from inside its own turn budget is also just the wrong tool for this
    # job -- the user runs `ollama serve` in their own terminal.
    return (
        f"Can't reach Ollama at {host} ({exc}). The local Ollama daemon isn't "
        "running. Do not try to start it yourself with execute_shell -- tell "
        "the user to run 'ollama serve' in a terminal, then retry."
    )


@dataclass
class HardwareBudget:
    ram_total_gb: float
    ram_free_gb: float
    gpu_name: str | None
    vram_total_gb: float | None
    vram_free_gb: float | None
    source: str  # "nvidia-smi", "rocm-smi", "windows-wmi", "macos-system_profiler", or "ram-only"
    cpu_brand: str = ""
    cpu_cores_physical: int = 0
    cpu_cores_logical: int = 0
    cpu_speed_ghz: float | None = None
    architecture: str = ""
    caveat: str = ""
    all_gpus: list = None  # (name, "dedicated"|"integrated") pairs, display-only


def _windows_cpu_name() -> str | None:
    """platform.processor() on Windows returns the raw identifier string
    ("Intel64 Family 6 Model 142...") not the marketing name -- ask WMI for
    the friendly one instead. Best-effort: any failure just falls through
    to the raw string."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Processor).Name"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


def _probe_cpu() -> tuple[str, int, int, float | None, str]:
    """Best-effort CPU identification. platform.processor() is often blank on
    Linux (returns the raw architecture string instead of a brand string) or,
    on Windows, a raw identifier rather than the marketing name -- _windows_cpu_name
    gets the friendly one there."""
    brand = None
    if sys.platform == "win32":
        brand = _windows_cpu_name()
    brand = brand or platform.processor() or platform.machine() or "unknown"
    arch = platform.machine() or "unknown"
    try:
        import psutil

        physical = psutil.cpu_count(logical=False) or 0
        logical = psutil.cpu_count(logical=True) or 0
        freq = psutil.cpu_freq()
        speed_ghz = round(freq.max / 1000, 2) if freq and freq.max else None
    except Exception:  # noqa: BLE001
        physical = logical = 0
        speed_ghz = None
    return brand, physical, logical, speed_ghz, arch


def _probe_gpu_nvidia() -> tuple[str | None, float | None, float | None, str]:
    """Returns (name, total_gb, free_gb, caveat); all None/"" when no NVIDIA
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


def _probe_gpu_amd_linux() -> tuple[str | None, float | None, float | None, str]:
    """AMD via rocm-smi, best-effort -- absent or unparseable output just
    means no GPU found here, never a raised exception."""
    smi = shutil.which("rocm-smi")
    if not smi:
        return None, None, None, ""
    try:
        name_out = subprocess.run(
            [smi, "--showproductname"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
        mem_out = subprocess.run(
            [smi, "--showmeminfo", "vram"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None, None, ""
    name_match = re.search(r"Card series:\s*(.+)", name_out)
    if not name_match:
        return None, None, None, ""
    name = name_match.group(1).strip()
    total_match = re.search(r"VRAM Total Memory \(B\):\s*(\d+)", mem_out)
    used_match = re.search(r"VRAM Total Used Memory \(B\):\s*(\d+)", mem_out)
    if not total_match:
        return name, None, None, ""
    try:
        total_gb = int(total_match.group(1)) / _GIB
        free_gb = (
            total_gb - int(used_match.group(1)) / _GIB if used_match else None
        )
    except ValueError:
        return name, None, None, ""
    return name, total_gb, free_gb, ""


def _probe_gpu_windows_wmi() -> tuple[str | None, float | None, float | None, str]:
    """Vendor-agnostic fallback on Windows (AMD, Intel, or any GPU Windows
    itself knows about) via WMI -- same best-effort PowerShell pattern as
    _windows_cpu_name()."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "Select-Object Name, AdapterRAM | ConvertTo-Json"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, None, None, ""
    if not out:
        return None, None, None, ""
    try:
        parsed = json.loads(out)
    except ValueError:
        return None, None, None, ""
    adapters = parsed if isinstance(parsed, list) else [parsed]
    adapters = [a for a in adapters if isinstance(a, dict) and a.get("Name")]
    if not adapters:
        return None, None, None, ""
    adapters.sort(key=lambda a: a.get("AdapterRAM") or 0, reverse=True)
    best = adapters[0]
    name = str(best["Name"])
    ram_bytes = best.get("AdapterRAM")
    if not ram_bytes:
        return name, None, None, ""
    total_gb = ram_bytes / _GIB
    # WMI's AdapterRAM for integrated graphics is typically a tiny "aperture"
    # (often ~1 GB), not real dedicated VRAM -- an iGPU shares system RAM, so
    # treating that figure as a hard budget scores worse than just falling
    # back to RAM. Every discrete GPU from the last decade reports well over
    # this threshold, so use it to tell "probably integrated" from "probably
    # discrete" and only trust the figure for the latter.
    if total_gb < 2.0:
        return name, None, None, ""
    caveat = ("Windows' WMI AdapterRAM figure is known to be inaccurate or "
              "capped (often at 4 GB) for some GPUs on Windows 10/11 -- "
              "treat this as a rough estimate.")
    return name, total_gb, None, caveat


def _probe_gpu_macos() -> tuple[str | None, float | None, float | None, str]:
    """macOS via system_profiler. Apple Silicon's unified memory has no
    separate dedicated VRAM figure, so total/free are left None there --
    probe_hardware()'s RAM-based scoring fallback already handles that
    correctly without double-counting."""
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None, None, ""
    try:
        parsed = json.loads(out)
    except ValueError:
        return None, None, None, ""
    displays = parsed.get("SPDisplaysDataType") or []
    if not displays or not isinstance(displays[0], dict):
        return None, None, None, ""
    card = displays[0]
    name = card.get("sppci_model") or card.get("_name")
    if not name:
        return None, None, None, ""
    vram_str = card.get("spdisplays_vram") or card.get("spdisplays_vram_shared")
    total_gb = None
    if vram_str:
        match = re.search(r"([\d.]+)\s*(GB|MB)", str(vram_str), re.IGNORECASE)
        if match:
            value, unit = float(match.group(1)), match.group(2).upper()
            total_gb = value / 1024 if unit == "MB" else value
    return name, total_gb, None, ""


def _probe_gpu() -> tuple[str | None, float | None, float | None, str, str]:
    """Try NVIDIA first (real free/total VRAM via nvidia-smi), then
    platform-appropriate vendor-agnostic fallbacks. Returns
    (name, total_gb, free_gb, caveat, source); name is None when nothing
    was found anywhere."""
    name, total_gb, free_gb, caveat = _probe_gpu_nvidia()
    if name:
        return name, total_gb, free_gb, caveat, "nvidia-smi"
    if sys.platform.startswith("linux"):
        name, total_gb, free_gb, caveat = _probe_gpu_amd_linux()
        if name:
            return name, total_gb, free_gb, caveat, "rocm-smi"
    elif sys.platform == "win32":
        name, total_gb, free_gb, caveat = _probe_gpu_windows_wmi()
        if name:
            return name, total_gb, free_gb, caveat, "windows-wmi"
    elif sys.platform == "darwin":
        name, total_gb, free_gb, caveat = _probe_gpu_macos()
        if name:
            return name, total_gb, free_gb, caveat, "macos-system_profiler"
    return None, None, None, "", "ram-only"


def probe_hardware() -> HardwareBudget:
    import psutil  # imported here so a missing/broken psutil only breaks this call

    vm = psutil.virtual_memory()
    gpu_name, vram_total, vram_free, caveat, gpu_source = _probe_gpu()
    cpu_brand, cpu_physical, cpu_logical, cpu_speed, arch = _probe_cpu()
    return HardwareBudget(
        ram_total_gb=round(vm.total / _GIB, 1),
        ram_free_gb=round(vm.available / _GIB, 1),
        gpu_name=gpu_name,
        vram_total_gb=round(vram_total, 1) if vram_total is not None else None,
        vram_free_gb=round(vram_free, 1) if vram_free is not None else None,
        source=gpu_source,
        cpu_brand=cpu_brand,
        cpu_cores_physical=cpu_physical,
        cpu_cores_logical=cpu_logical,
        cpu_speed_ghz=cpu_speed,
        architecture=arch,
        caveat=caveat,
        all_gpus=_probe_all_gpus(),
    )


def _probe_all_gpus() -> list[tuple[str, str]]:
    """(name, kind) for every adapter Windows knows about -- display-only
    inventory for the Dedicated/Integrated lines. Best-effort."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "Select-Object Name, AdapterRAM | ConvertTo-Json"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
        parsed = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    adapters = parsed if isinstance(parsed, list) else [parsed]
    out = []
    for a in adapters:
        if not (isinstance(a, dict) and a.get("Name")):
            continue
        ram_gb = (a.get("AdapterRAM") or 0) / _GIB
        out.append((str(a["Name"]), "dedicated" if ram_gb >= 2.0 else "integrated"))
    return out


# name -> (approx params in billions, approx download size in GB at the
# quant Ollama ships by default -- mostly Q4_K_M). Rough figures for a fit
# *recommendation* on models not yet pulled; Ollama's own daemon is the
# authority on whether a pull actually fits at pull/load time. Anything the
# user has ALREADY pulled gets real numbers instead -- see
# _installed_model_entries() and _model_catalog(), which merges the two.
_STATIC_MODEL_CATALOG = [
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

_PARAM_SIZE_RE = re.compile(r"([\d.]+)\s*([MB])", re.IGNORECASE)


def _installed_model_entries() -> list[tuple[str, float, float]]:
    """Real (name, params_b, size_gb) for every locally-pulled model, straight
    from the already-running Ollama daemon (/api/tags) -- no approximation
    needed, it already knows exactly what it downloaded. Best-effort: the
    daemon may not be running (hardware probing works without it), so any
    failure here just means an empty list, never a raised error."""
    try:
        installed = list_installed_models()
    except OllamaError:
        return []
    entries = []
    for model in installed:
        name = model.get("name") or model.get("model")
        if not name:
            continue
        size_bytes = model.get("size")
        size_gb = round(size_bytes / _GIB, 1) if isinstance(size_bytes, (int, float)) else None
        param_str = (model.get("details") or {}).get("parameter_size") or ""
        match = _PARAM_SIZE_RE.match(param_str.strip())
        # "751.63M" -> 0.75B; Ollama reports sub-billion params with an M suffix
        params_b = float(match.group(1)) / 1000 if (match and match.group(2).upper() == "M") else (float(match.group(1)) if match else None)
        if size_gb is None or size_gb <= 0 or params_b is None:
            continue  # cloud/unknown-size entries cannot be scored as local models
        entries.append((name, round(params_b, 2), size_gb))
    return entries


def _model_catalog() -> list[tuple[str, float, float]]:
    """The models the user can actually run locally, with real specs. When the
    Ollama daemon has models pulled, ONLY those are shown -- no hardcoded
    recommendation list mixed in. The static catalog is a fallback for a
    machine with nothing pulled yet (fresh installs, daemon down)."""
    installed = _installed_model_entries()
    if installed:
        return installed
    return list(_STATIC_MODEL_CATALOG)

# Rough runtime overhead (KV cache, framework buffers) on top of raw weight
# size before a model is comfortable, not just loadable.
_HEADROOM_FACTOR = 1.3

# Score bands, matching the Compatible/Marginal/Poor split used by hardware
# fit checkers like llm-checker (github.com/signerless/llm-checker).
COMPATIBLE_THRESHOLD = 75
MARGINAL_THRESHOLD = 60


def hardware_tier(hw: "HardwareBudget") -> str:
    """Display-only coarse tier, matching llm-checker's bands. A dedicated
    GPU jumps a tier -- the budget that matters for local inference is
    VRAM on GPU, RAM on CPU."""
    budget = hw.vram_total_gb or hw.ram_total_gb
    if hw.vram_total_gb:
        if budget >= 16: return "HIGH"
        if budget >= 8: return "MEDIUM HIGH"
        return "MEDIUM LOW"
    if budget >= 32: return "MEDIUM HIGH"
    if budget >= 12: return "MEDIUM LOW"
    if budget >= 8: return "LOW"
    return "ULTRA LOW"


def backend_label(hw: "HardwareBudget") -> str:
    """Display-only runtime backend guess, llm-checker style."""
    if not hw.gpu_name:
        return "CPU"
    if hw.source == "nvidia-smi" and hw.vram_total_gb:
        return "CUDA"
    if hw.source == "rocm-smi" and hw.vram_total_gb:
        return "ROCm"
    if sys.platform == "darwin":
        return "Metal"
    # integrated/unknown VRAM: inference runs on CPU, the iGPU's media
    # blocks only assist -- the honest label, llm-checker shows the same
    return "CPU + Vulkan assist"


@dataclass
class ModelScore:
    name: str
    params_b: float
    size_gb: float
    score: int  # 0-100
    category: str  # "Compatible", "Marginal", "Poor"


def _fmt_params(params_b: float) -> str:
    """"7" for a whole number, "7.2" for a real (installed-model) fractional
    size -- keeps the static catalog's clean look while still showing exact
    figures for locally-installed models."""
    return f"{params_b:g}"


def _category(score: int) -> str:
    if score >= COMPATIBLE_THRESHOLD:
        return "Compatible"
    if score >= MARGINAL_THRESHOLD:
        return "Marginal"
    return "Poor"


def score_models(hw: HardwareBudget) -> list[ModelScore]:
    """0-100 fit score per model, sorted best-first. Catalog is the static
    recommendation list with any locally-installed model's real specs
    overlaid -- see _model_catalog().

    A GPU budgets from VRAM (fast, so the memory-fit ratio IS the score).
    No GPU falls back to free RAM and additionally caps the score by core
    count -- CPU inference throughput tracks cores/memory-bandwidth, not
    just whether the weights fit in RAM at all, so an 8+ physical-core
    machine keeps the full memory-fit score while a 2-core one is capped
    near 60% of it even for a model that technically fits."""
    if hw.vram_total_gb:
        budget_gb = hw.vram_total_gb
        cpu_cap = 1.0
    else:
        budget_gb = hw.ram_free_gb
        cores = hw.cpu_cores_physical or 0
        cpu_cap = 0.6 + 0.4 * min(1.0, cores / 8)

    scores = []
    for name, params_b, size_gb in _model_catalog():
        if size_gb <= 0:
            continue
        fit_ratio = budget_gb / (size_gb * _HEADROOM_FACTOR)
        raw = min(100.0, 100.0 * fit_ratio) * cpu_cap
        score = max(0, round(raw))
        scores.append(ModelScore(name, params_b, size_gb, score, _category(score)))
    scores.sort(key=lambda m: m.score, reverse=True)
    return scores


def recommend_models(hw: HardwareBudget) -> list[str]:
    """Back-compat plain-text summary of score_models(), Compatible/Marginal only."""
    mode = "GPU" if hw.vram_total_gb else "CPU, no GPU detected -- will be slow"
    lines = [
        f"{m.name} ({_fmt_params(m.params_b)}B, ~{m.size_gb:.1f} GB) -- score {m.score}/100, "
        f"{m.category} [{mode}]"
        for m in score_models(hw) if m.category != "Poor"
    ]
    if not lines:
        best = score_models(hw)[0]
        lines.append(
            f"Even {best.name} (~{best.size_gb:.1f} GB) scores only {best.score}/100 -- "
            f"nothing in the catalog fits comfortably on this machine."
        )
    return lines


def format_hardware_report(hw: HardwareBudget) -> str:
    lines = ["=== System ==="]
    cpu_line = f"CPU: {hw.cpu_brand or 'unknown'}"
    if hw.cpu_cores_physical:
        cpu_line += f" ({hw.cpu_cores_physical} cores"
        if hw.cpu_cores_logical and hw.cpu_cores_logical != hw.cpu_cores_physical:
            cpu_line += f" / {hw.cpu_cores_logical} threads"
        if hw.cpu_speed_ghz:
            cpu_line += f", {hw.cpu_speed_ghz} GHz"
        cpu_line += ")"
    lines.append(cpu_line)
    lines.append(f"Architecture: {hw.architecture or 'unknown'}")
    lines.append(f"RAM: {hw.ram_free_gb:.1f} GB free / {hw.ram_total_gb:.1f} GB total")
    if hw.gpu_name:
        tag = " (dedicated)" if hw.vram_total_gb is not None else ""
        lines.append(f"GPU: {hw.gpu_name}{tag}")
        if hw.vram_total_gb is not None:
            free = f"{hw.vram_free_gb:.1f} GB free / " if hw.vram_free_gb is not None else ""
            lines.append(f"VRAM: {free}{hw.vram_total_gb:.1f} GB total")
        else:
            lines.append("VRAM: unknown -- GPU name detected but no VRAM figure "
                          "was reported")
        if hw.caveat:
            lines.append(f"Note: {hw.caveat}")
    else:
        lines.append("GPU: none detected")
        lines.append("VRAM: n/a -- local models run on CPU")
    lines.append("")
    lines.append("=== Model Compatibility ===")
    lines.append(f"{'Model':<22} {'Params':>7} {'Size':>9} {'Score':>7}  Category")
    for m in score_models(hw):
        lines.append(
            f"{m.name:<22} {_fmt_params(m.params_b):>4}B {m.size_gb:>7.1f}GB {m.score:>5}/100  {m.category}"
        )
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
    except urllib.error.HTTPError as exc:
        # HTTPError subclasses URLError, but it means the daemon answered: a
        # 404 from /api/delete is "no such model", not "Ollama isn't running".
        try:
            detail = json.loads(exc.read() or b"{}").get("error", "")
        except (ValueError, OSError):
            detail = ""
        name = (body or {}).get("model") or (body or {}).get("name") or ""
        if exc.code == 404 and name:
            raise OllamaError(f"Model '{name}' is not installed (see /local list).") from exc
        raise OllamaError(f"Ollama returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise OllamaError(_daemon_unreachable_message(host, exc)) from exc
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
        raise OllamaError(_daemon_unreachable_message(host, exc)) from exc
    return last_status or "pull finished"


def pull_model_stream(name: str, host: str = DEFAULT_OLLAMA_HOST, timeout: int = 600):
    """Yield each NDJSON event from /api/pull as it arrives.

    Each event is a dict with at least a ``status`` key; download events
    also contain ``completed`` (int, bytes so far) and ``total`` (int,
    bytes for the current layer).  Raises ``OllamaError`` on failure."""
    name = _validate_model_name(name)
    url = f"{host.rstrip('/')}/api/pull"
    req = urllib.request.Request(
        url, data=json.dumps({"model": name}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
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
                yield evt
    except urllib.error.URLError as exc:
        raise OllamaError(_daemon_unreachable_message(host, exc)) from exc


def remove_model(name: str, host: str = DEFAULT_OLLAMA_HOST) -> str:
    name = _validate_model_name(name)
    _ollama_request("/api/delete", method="DELETE", body={"model": name}, host=host)
    return f"Removed {name}"


# --- Browse ollama.com's public catalog (the daemon can't enumerate it) ---

OLLAMA_CATALOG_URL = "https://ollama.com/search"

_FAMILY_RE = re.compile(r'href="/library/([a-zA-Z0-9._:-]+)"')
_TAG_ROW_RE = re.compile(
    r'<div class="hidden group px-4 py-3 sm:grid.*?</div>', re.DOTALL)
_TAG_NAME_RE = re.compile(r'>([a-zA-Z0-9._:-]+:[a-zA-Z0-9._:-]+)</a>')
_SIZE_RE = re.compile(r'([\d.]+)\s*(GB|MB|KB)')


def _fetch(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "agent8088"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_catalog_families(html: str) -> list[str]:
    """Family names in page order from ollama.com/search -- top-of-list is
    most popular, so first-seen order is the ranking."""
    seen = []
    for match in _FAMILY_RE.finditer(html):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def parse_library_tags(html: str) -> list[tuple[str, float]]:
    """(tag, size_gb) pairs from a /library/<family> page. The desktop tag
    table rows carry both; the mobile rows duplicate data, so matching on
    the desktop grid rows only."""
    tags = []
    for row in _TAG_ROW_RE.finditer(html):
        name_match = _TAG_NAME_RE.search(row.group(0))
        size_match = _SIZE_RE.search(row.group(0))
        if not name_match or not size_match:
            continue
        value, unit = float(size_match.group(1)), size_match.group(2)
        size_gb = value / 1024 if unit == "MB" else value / 1024 / 1024 if unit == "KB" else value
        tags.append((name_match.group(1), round(size_gb, 2)))
    return tags


def available_models(hw: HardwareBudget, query: str = "", limit: int = 10) -> list[ModelScore]:
    """Browse ollama.com's catalog live and score pullable tags against this
    machine, best-fit first. Returns at least `limit` COMPATIBLE models if
    they exist: when the popular families are all too big for this machine,
    keeps scanning further down the library (batch by batch) instead of
    padding with Poor entries. The daemon itself can't list the registry
    (no /api/search endpoint), so ollama.com's server-rendered pages are the
    source. No query browses the full /library catalog by popularity; a query
    uses the /search page."""
    if query:
        url = f"{OLLAMA_CATALOG_URL}?q={urllib.parse.quote(query)}"
    else:
        url = "https://ollama.com/library"
    families = parse_catalog_families(_fetch(url))
    budget_gb = hw.vram_total_gb or hw.ram_free_gb

    def _score_family(family: str) -> list[ModelScore]:
        try:
            page = _fetch(f"https://ollama.com/library/{family}")
        except OSError:
            return []
        # embedding models can't chat -- leave them out of recommendations
        # (llm-checker excludes them too); the family page's own description
        # names them
        if re.search(r"embedding", page, re.IGNORECASE):
            return []
        out = []
        for name, size_gb in parse_library_tags(page):
            fit_ratio = budget_gb / (size_gb * _HEADROOM_FACTOR)
            score = max(0, round(min(100.0, 100.0 * fit_ratio)))
            out.append(ModelScore(name, 0.0, size_gb, score, _category(score)))
        return out

    # one best tag per family so a many-tag family doesn't crowd the list;
    # scan family batches until `limit` compatible models are found
    from concurrent.futures import ThreadPoolExecutor
    best_per_family: dict[str, ModelScore] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for start in range(0, len(families), 24):
            batch = families[start:start + 24]
            per_family = pool.map(_score_family, batch)
            for fam, scored in zip(batch, per_family):
                if scored:
                    best_per_family[fam] = max(scored, key=lambda m: m.score)
            compatible = [m for m in best_per_family.values() if m.category == "Compatible"]
            if len(compatible) >= limit:
                break
    scores = sorted(best_per_family.values(), key=lambda m: m.score, reverse=True)
    compatible = [m for m in scores if m.category == "Compatible"]
    # ponytail: compatible-only when we have enough; small libraries (query
    # hits) fall back to all scores rather than an empty list
    return compatible[:limit] if len(compatible) >= limit else scores[:limit]
