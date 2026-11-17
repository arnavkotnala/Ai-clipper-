"""ai-clipper: the right hardware profile is chosen on each machine (simulated detection)."""

from types import SimpleNamespace

import pytest

from sharkyclips import hardware, hwprofile


def fake_hw(gpu_name=None, vram=0.0, cpu="cpu", ram=16.0):
    gpu = SimpleNamespace(name=gpu_name, vram_gb=vram) if gpu_name else None
    return SimpleNamespace(best_nvidia=gpu, cpu_name=cpu, ram_total_gb=ram)


@pytest.mark.parametrize("gpu,vram,expected", [
    ("NVIDIA GeForce RTX 5050 Laptop GPU", 8.0, "rtx5050-laptop"),
    ("NVIDIA GeForce RTX 5060 Laptop GPU", 8.0, "rtx5060-laptop"),
    ("NVIDIA GeForce RTX 4070 Laptop GPU", 8.0, "nvidia-8gb"),
    ("NVIDIA GeForce GTX 1650", 4.0, "cpu-only"),
    (None, 0.0, "cpu-only"),
])
def test_profile_selection(monkeypatch, gpu, vram, expected):
    monkeypatch.setattr(hardware, "detect_hardware", lambda: fake_hw(gpu, vram))
    hwprofile.active.cache_clear()
    try:
        assert hwprofile.active()["name"] == expected
    finally:
        hwprofile.active.cache_clear()


def test_both_laptops_use_the_same_models():
    profiles = {p["name"]: p for p in hwprofile._load_all()}
    a, b = profiles["rtx5050-laptop"]["settings"], profiles["rtx5060-laptop"]["settings"]
    assert a["vlm_model"] == b["vlm_model"] and a["vlm_frames"] == b["vlm_frames"]


def test_tests_do_not_get_profile_settings():
    assert hwprofile.settings() == {}
