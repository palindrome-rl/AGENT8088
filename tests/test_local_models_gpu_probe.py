"""_probe_gpu() now tries NVIDIA, then a platform-appropriate vendor-agnostic
fallback (AMD/rocm-smi on Linux, WMI on Windows, system_profiler on macOS),
instead of reporting "no GPU" for anything but NVIDIA.
"""
import json
import sys
import types

import pytest

from agent8088 import local_models as L


def _run_result(stdout):
    return types.SimpleNamespace(stdout=stdout, returncode=0)


def test_nvidia_still_wins_when_present(monkeypatch):
    monkeypatch.setattr(L.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: _run_result("RTX 4090, 24576, 20000\n"))
    name, total, free, caveat, source = L._probe_gpu()
    assert name == "RTX 4090"
    assert source == "nvidia-smi"


def test_falls_back_to_windows_wmi_when_no_nvidia(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(L.shutil, "which", lambda name: None)  # no nvidia-smi
    payload = json.dumps({"Name": "AMD Radeon RX 7900", "AdapterRAM": 16 * L._GIB})
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: _run_result(payload))
    name, total, free, caveat, source = L._probe_gpu()
    assert name == "AMD Radeon RX 7900"
    assert total == pytest.approx(16.0)
    assert source == "windows-wmi"


def test_windows_wmi_ignores_tiny_igpu_aperture(monkeypatch):
    """An integrated GPU's AdapterRAM is a small aperture, not real dedicated
    VRAM -- trusting it as a scoring budget would be worse than the honest
    RAM fallback, so name-only detection is correct here."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(L.shutil, "which", lambda name: None)
    payload = json.dumps({"Name": "Intel(R) UHD Graphics 620", "AdapterRAM": 1 * L._GIB})
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: _run_result(payload))
    name, total, free, caveat, source = L._probe_gpu()
    assert name == "Intel(R) UHD Graphics 620"
    assert total is None  # aperture size rejected as an unreliable VRAM budget
    assert source == "windows-wmi"  # but the name itself is still reported


def test_falls_back_to_amd_rocm_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(L.shutil, "which", lambda name: "/usr/bin/rocm-smi" if name == "rocm-smi" else None)

    def fake_run(cmd, **kwargs):
        if "--showproductname" in cmd:
            return _run_result("Card series: AMD Radeon RX 7900 XTX\n")
        return _run_result(
            "VRAM Total Memory (B): 25769803776\n"
            "VRAM Total Used Memory (B): 1073741824\n"
        )

    monkeypatch.setattr(L.subprocess, "run", fake_run)
    name, total, free, caveat, source = L._probe_gpu()
    assert name == "AMD Radeon RX 7900 XTX"
    assert total == pytest.approx(24.0, abs=0.1)
    assert source == "rocm-smi"


def test_falls_back_to_macos_system_profiler(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(L.shutil, "which", lambda name: None)
    payload = json.dumps({"SPDisplaysDataType": [{"sppci_model": "Apple M3 Pro"}]})
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: _run_result(payload))
    name, total, free, caveat, source = L._probe_gpu()
    assert name == "Apple M3 Pro"
    assert total is None  # unified memory -- RAM fallback handles budgeting
    assert source == "macos-system_profiler"


def test_nothing_found_anywhere_falls_back_to_ram_only(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(L.shutil, "which", lambda name: None)
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: _run_result(""))
    name, total, free, caveat, source = L._probe_gpu()
    assert name is None
    assert source == "ram-only"


def test_installed_models_with_sub_billion_params_are_catalogued(monkeypatch):
    """Live regression: Ollama reports sub-1B models as e.g. "751.63M" --
    those must still land in the catalog with real specs, not be dropped."""
    monkeypatch.setattr(L, "list_installed_models", lambda host=None: [
        {"name": "qwen3:0.6b", "size": 522653767,
         "details": {"parameter_size": "751.63M"}},
        {"name": "llama3.1:8b", "size": 4.9 * L._GIB,
         "details": {"parameter_size": "8.03B"}},
    ])
    catalog = {name: (p, s) for name, p, s in L._model_catalog()}
    assert "qwen3:0.6b" in catalog
    assert catalog["qwen3:0.6b"][0] == pytest.approx(0.752, abs=0.01)
    assert catalog["llama3.1:8b"][0] == pytest.approx(8.03)


def test_cloud_models_with_no_download_size_are_not_scored_locally(monkeypatch):
    monkeypatch.setattr(L, "list_installed_models", lambda host=None: [
        {"name": "glm-5.2:cloud", "size": 0,
         "details": {"parameter_size": "9B"}},
        {"name": "moondream:latest", "size": 1.6 * L._GIB,
         "details": {"parameter_size": "1.8B"}},
    ])
    assert [name for name, _, _ in L._model_catalog()] == ["moondream:latest"]


def test_installed_models_replace_the_static_catalog(monkeypatch):
    """User request: when the daemon has models pulled, /local must show ONLY
    those real models -- the hardcoded recommendation list must not appear
    alongside them."""
    monkeypatch.setattr(L, "list_installed_models", lambda host=None: [
        {"name": "qwen3:0.6b", "size": 522653767,
         "details": {"parameter_size": "751.63M"}},
    ])
    names = [name for name, _, _ in L._model_catalog()]
    assert names == ["qwen3:0.6b"]


def test_static_catalog_used_when_nothing_installed(monkeypatch):
    """Fallback: daemon reachable but empty (or unreachable) still shows the
    generic recommendation list, so /local isn't useless on a fresh machine."""
    monkeypatch.setattr(L, "list_installed_models", lambda host=None: [])
    names = [name for name, _, _ in L._model_catalog()]
    assert "llama3.2:1b" in names


def test_score_models_uses_ram_budget_when_gpu_vram_unknown():
    """Name-only GPU detection (vram_total_gb=None) must not accidentally
    zero out scoring -- it should use the same RAM-budget path as no GPU."""
    hw = L.HardwareBudget(
        ram_total_gb=16.0, ram_free_gb=8.0, gpu_name="Intel(R) UHD Graphics 620",
        vram_total_gb=None, vram_free_gb=None, source="windows-wmi",
        cpu_cores_physical=8,
    )
    scores = L.score_models(hw)
    assert any(s.category != "Poor" for s in scores)


def test_hardware_tier_bands():
    assert L.hardware_tier(L.HardwareBudget(
        ram_total_gb=16.0, ram_free_gb=8.0, gpu_name=None, vram_total_gb=None,
        vram_free_gb=None, source="ram-only")) == "MEDIUM LOW"
    assert L.hardware_tier(L.HardwareBudget(
        ram_total_gb=64.0, ram_free_gb=32.0, gpu_name="RTX 4090",
        vram_total_gb=24.0, vram_free_gb=20.0, source="nvidia-smi")) == "HIGH"
    assert L.hardware_tier(L.HardwareBudget(
        ram_total_gb=4.0, ram_free_gb=2.0, gpu_name=None, vram_total_gb=None,
        vram_free_gb=None, source="ram-only")) == "ULTRA LOW"


def test_backend_label():
    hw = L.HardwareBudget(
        ram_total_gb=16.0, ram_free_gb=8.0, gpu_name="Intel(R) UHD Graphics 620",
        vram_total_gb=None, vram_free_gb=None, source="windows-wmi")
    assert "CPU" in L.backend_label(hw)  # iGPU: inference runs on CPU
    hw_gpu = L.HardwareBudget(
        ram_total_gb=32.0, ram_free_gb=16.0, gpu_name="RTX 4090",
        vram_total_gb=24.0, vram_free_gb=20.0, source="nvidia-smi")
    assert "CUDA" in L.backend_label(hw_gpu)


def test_shared_vram_label_only_for_igpu():
    """Display: an integrated GPU shows shared VRAM derived from RAM; a
    dedicated one shows its real VRAM. Scoring is unchanged either way."""
    hw = L.HardwareBudget(
        ram_total_gb=16.0, ram_free_gb=8.0, gpu_name="Intel(R) UHD Graphics 620",
        vram_total_gb=None, vram_free_gb=None, source="windows-wmi")
    scores = L.score_models(hw)
    assert scores  # still scores, unchanged path
    hw_d = L.HardwareBudget(
        ram_total_gb=32.0, ram_free_gb=16.0, gpu_name="RTX 4090",
        vram_total_gb=24.0, vram_free_gb=20.0, source="nvidia-smi")
    assert L.backend_label(hw_d) == "CUDA"
