# -*- coding: utf-8 -*-
"""test_deps — 组件定位 / 缺失检测 / 清单双语（v1.4：pip 组检测已随专用运行时移除）"""
import deps
import messages


def test_human_size():
    assert deps._human_size(0) == "未知大小"
    assert deps._human_size(500) == "500 B"
    assert deps._human_size(1024 * 1024) == "1.0 MB"
    assert deps._human_size(1_620_000_000) == "1.5 GB"


def test_ggml_map_and_model_path(monkeypatch, tmp_path):
    monkeypatch.setenv("SMART_SUMMARIZE_WHISPERCPP_MODELS_DIR", str(tmp_path))
    # 重新读取模块级常量
    monkeypatch.setattr(deps, "WHISPERCPP_MODELS_DIR", tmp_path)
    monkeypatch.setattr(deps, "MANAGED_MODELS", tmp_path / "managed")
    assert deps._find_model_file("large-v3-turbo") is None
    f = tmp_path / "ggml-large-v3-turbo.bin"
    f.write_bytes(b"x")
    assert deps._find_model_file("large-v3-turbo") == f
    assert deps._find_model_file("unknown-model") is None


def test_find_ffmpeg_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"x")
    monkeypatch.setenv("SMART_SUMMARIZE_FFMPEG", str(fake))
    assert deps._find_ffmpeg() == str(fake)
    monkeypatch.setenv("SMART_SUMMARIZE_FFMPEG", str(tmp_path / "nope.exe"))
    # 回退 PATH/受管目录——只验证不抛异常且返回 str 或 None
    assert deps._find_ffmpeg() is None or isinstance(deps._find_ffmpeg(), str)


def test_missing_dep_kinds_all_found(tmp_path, monkeypatch):
    ff = tmp_path / "ffmpeg.exe"
    ff.write_bytes(b"x")
    cli = tmp_path / "whisper-cli.exe"
    cli.write_bytes(b"x")
    model = tmp_path / "ggml-large-v3-turbo.bin"
    model.write_bytes(b"x")
    monkeypatch.setenv("SMART_SUMMARIZE_FFMPEG", str(ff))
    monkeypatch.setenv("SMART_SUMMARIZE_WHISPERCPP_CLI", str(cli))
    monkeypatch.setenv("SMART_SUMMARIZE_WHISPERCPP_MODELS_DIR", str(tmp_path))
    monkeypatch.setattr(deps, "WHISPERCPP_MODELS_DIR", tmp_path)
    monkeypatch.setattr(deps, "MANAGED_MODELS", tmp_path)
    assert deps._missing_dep_kinds("large-v3-turbo") == []


def test_dep_detail_ffmpeg_and_model(tmp_path):
    messages.set_lang("zh")
    d = deps._dep_detail("ffmpeg")
    assert d["purpose"] == "音视频解码与音频提取"
    messages.set_lang("en")
    d = deps._dep_detail("ffmpeg")
    assert d["purpose"] == "Audio/video decoding and audio extraction"
    messages.set_lang("zh")
    d = deps._dep_detail("model:large-v3-turbo")
    assert d["purpose"] == "whisper 转录模型 (large-v3-turbo)"
    assert d["source"].startswith("https://huggingface.co/")
    assert d["est_size"]  # 已知大小或实际大小


def test_dep_detail_runtime(tmp_path):
    """专用运行时作为组件出现在确认 UI（v1.4 §8B）"""
    messages.set_lang("zh")
    d = deps._dep_detail("runtime")
    assert d["purpose"].startswith("技能专用 Python 运行时")
    assert "runtime/" in d["source"]
    messages.set_lang("en")
    d = deps._dep_detail("runtime")
    assert d["purpose"].startswith("Skill-dedicated Python runtime")


def test_install_dep_unknown_kind():
    import pytest
    with pytest.raises(RuntimeError):
        deps._install_dep("nope:kind")
    with pytest.raises(RuntimeError):
        deps._install_dep("pip:excel")  # v1.4：pip 组机制已随专用运行时移除


def test_cublas_assets_restored():
    # NVIDIA cublas 预编译链路曾因常量缺失 NameError（拆分前即存在），此为回归闸
    assert deps.WHISPERCPP_CUBLAS_ASSETS == [
        "whisper-cublas-11.8.0-bin-x64.zip",
        "whisper-cublas-12.4.0-bin-x64.zip",
    ]
