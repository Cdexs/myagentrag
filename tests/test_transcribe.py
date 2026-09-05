# -*- coding: utf-8 -*-
"""test_transcribe — 后端识别 / 依赖缺失路径（离线，不真转录）"""
import pytest

import transcribe
from deps import MissingDependencyError
from transcribe import _detect_backend_from_log, _whispercpp_transcribe


def test_backend_vulkan_new_format():
    log = "ggml_vulkan: 0 | AMD Radeon 780M Graphics (AMD proprietary driver) | uma: 1"
    assert "Vulkan: AMD Radeon 780M" in _detect_backend_from_log(log)


def test_backend_vulkan_old_format():
    log = "ggml_vulkan: 0 = AMD Radeon(R) Graphics"
    assert "Vulkan: AMD Radeon(R) Graphics" in _detect_backend_from_log(log)


def test_backend_cuda_metal_rocm_none():
    assert "CUDA" in _detect_backend_from_log("ggml_cuda init: device 0 | NVIDIA RTX")
    assert _detect_backend_from_log("ggml_metal: Metal compiler built-in") == "Metal"
    assert _detect_backend_from_log("ggml_hip: ROCm 6.1") == "ROCm/HIP"
    assert _detect_backend_from_log("system info: AVX = 1") is None
    assert _detect_backend_from_log("") is None


def test_transcribe_returns_none_when_deps_missing(monkeypatch):
    monkeypatch.setattr(transcribe, "_find_ffmpeg", lambda: None)
    monkeypatch.setattr(transcribe, "_find_whispercpp_cli", lambda: None)
    monkeypatch.setattr(transcribe, "_find_model_file", lambda m: None)
    assert _whispercpp_transcribe("x.wav", "large-v3-turbo", want_srt=False) is None


def test_extract_audio_text_raises_missing_deps(monkeypatch):
    monkeypatch.setattr(transcribe, "_missing_dep_kinds", lambda m: ["ffmpeg"])
    with pytest.raises(MissingDependencyError):
        transcribe.extract_audio_text("x.wav")


def test_extract_video_text_missing_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(transcribe, "_find_ffmpeg", lambda: None)
    with pytest.raises(MissingDependencyError):
        transcribe.extract_video_text(str(tmp_path / "v.mp4"))


def test_no_gpu_flag_is_module_state():
    transcribe.NO_GPU = True
    assert transcribe.NO_GPU is True
    transcribe.NO_GPU = False  # 复原
