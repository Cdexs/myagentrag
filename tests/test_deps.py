# -*- coding: utf-8 -*-
"""test_deps — 组件定位 / 缺失检测 / 清单双语（v1.4：pip 组检测已随专用运行时移除）"""
from pathlib import Path

import deps
import messages


def test_human_size():
    assert deps._human_size(0) == "未知大小"
    assert deps._human_size(500) == "500 B"
    assert deps._human_size(1024 * 1024) == "1.0 MB"
    assert deps._human_size(1_620_000_000) == "1.5 GB"


def test_ggml_map_and_model_path(monkeypatch, tmp_path):
    monkeypatch.setenv("MYAGENTRAG_WHISPERCPP_MODELS_DIR", str(tmp_path))
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
    monkeypatch.setenv("MYAGENTRAG_FFMPEG", str(fake))
    assert deps._find_ffmpeg() == str(fake)
    monkeypatch.setenv("MYAGENTRAG_FFMPEG", str(tmp_path / "nope.exe"))
    # 回退 PATH/受管目录——只验证不抛异常且返回 str 或 None
    assert deps._find_ffmpeg() is None or isinstance(deps._find_ffmpeg(), str)


def test_missing_dep_kinds_all_found(tmp_path, monkeypatch):
    ff = tmp_path / "ffmpeg.exe"
    ff.write_bytes(b"x")
    cli = tmp_path / "whisper-cli.exe"
    cli.write_bytes(b"x")
    model = tmp_path / "ggml-large-v3-turbo.bin"
    model.write_bytes(b"x")
    monkeypatch.setenv("MYAGENTRAG_FFMPEG", str(ff))
    monkeypatch.setenv("MYAGENTRAG_WHISPERCPP_CLI", str(cli))
    monkeypatch.setenv("MYAGENTRAG_WHISPERCPP_MODELS_DIR", str(tmp_path))
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


def test_install_model_reuses_any_known_copy(tmp_path, monkeypatch):
    """回归（端侧审计发现）：模型在任一已知位置（环境变量目录/受管目录）已存在时，
    install_model 必须直接复用，不得对同一模型重复下载 1.5GB。"""
    existing = tmp_path / "known" / "ggml-large-v3-turbo.bin"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"x")
    monkeypatch.setattr(deps, "WHISPERCPP_MODELS_DIR", existing.parent)
    monkeypatch.setattr(deps, "MANAGED_MODELS", tmp_path / "managed")
    monkeypatch.delenv("MYAGENTRAG_WHISPERCPP_MODELS_DIR", raising=False)

    def no_download(*a, **kw):
        raise AssertionError("模型已存在时不应触发下载")
    monkeypatch.setattr(deps, "_http_download", no_download)
    assert deps.install_model("large-v3-turbo") == existing


def test_install_model_downloads_into_env_dir_when_nowhere(tmp_path, monkeypatch):
    """所有已知位置都没有模型时，下载到环境变量指定的目录"""
    env_dir = tmp_path / "envmodels"
    monkeypatch.setenv("MYAGENTRAG_WHISPERCPP_MODELS_DIR", str(env_dir))
    monkeypatch.setattr(deps, "WHISPERCPP_MODELS_DIR", env_dir)
    monkeypatch.setattr(deps, "MANAGED_MODELS", tmp_path / "managed")

    def fake_download(url, dest, desc=""):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"model")
        return dest
    monkeypatch.setattr(deps, "_http_download", fake_download)
    out = deps.install_model("large-v3-turbo")
    assert out == env_dir / "ggml-large-v3-turbo.bin" and out.exists()


def test_llama_embed_zip_install_isolated_dlls(tmp_path, monkeypatch):
    """v1.5：Windows zip 安装 → 引擎与 DLL 隔离在 bin/llama/ 子目录（不污染 whisper-cli）"""
    import zipfile
    fake_zip = tmp_path / "llama.zip"
    with zipfile.ZipFile(fake_zip, "w") as zf:
        zf.writestr("llama-b10819/bin/llama-embedding.exe", b"x")
        zf.writestr("llama-b10819/bin/llama-server.exe", b"x")
        zf.writestr("llama-b10819/bin/ggml.dll", b"x")

    def fake_download(url, dest, desc=""):
        import shutil
        shutil.copy2(fake_zip, dest)
        return dest
    monkeypatch.setattr(deps, "_http_download", fake_download)
    monkeypatch.setattr(deps, "MANAGED_BIN", tmp_path / "bin")
    monkeypatch.setattr(deps, "_find_llama_embed", lambda: None)
    monkeypatch.setattr(deps, "_detect_gpu", lambda: ("nvidia", "Fake GPU", ""))
    monkeypatch.setattr(deps.os, "name", "nt")
    out = deps.install_llama_embed()
    assert str(out).replace("\\", "/").endswith("bin/llama/llama-server.exe")
    assert (tmp_path / "bin" / "llama" / "ggml.dll").exists()
    assert not (tmp_path / "bin" / "ggml.dll").exists()  # DLL 不进 bin/ 根


def test_embedding_model_download_then_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "MANAGED_MODELS", tmp_path / "models")
    calls = []

    def fake_download(url, dest, desc=""):
        calls.append(url)
        from pathlib import Path as P
        dest = P(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"gguf")
        return dest
    monkeypatch.setattr(deps, "_http_download", fake_download)
    mid = "Qwen3-Embedding-0.6B"
    p1 = deps.install_embedding_model(mid)
    p2 = deps.install_embedding_model(mid)
    assert p1 == p2 and p1.exists() and len(calls) == 1  # 已存在不重复下载
    assert "Qwen/Qwen3-Embedding-0.6B-GGUF" in calls[0]


def test_missing_kb_kinds_chain(tmp_path, monkeypatch):
    """组件检测回归：ffmpeg/whisper-cli/ggml 经环境变量指向临时文件 → 全部就绪"""
    ff = tmp_path / "ffmpeg.exe"
    ff.write_bytes(b"x")
    cli = tmp_path / "whisper-cli.exe"
    cli.write_bytes(b"x")
    model = tmp_path / "ggml-large-v3-turbo.bin"
    model.write_bytes(b"x")
    monkeypatch.setenv("MYAGENTRAG_FFMPEG", str(ff))
    monkeypatch.setenv("MYAGENTRAG_WHISPERCPP_CLI", str(cli))
    monkeypatch.setenv("MYAGENTRAG_WHISPERCPP_MODELS_DIR", str(tmp_path))
    monkeypatch.setattr(deps, "WHISPERCPP_MODELS_DIR", tmp_path)
    monkeypatch.setattr(deps, "MANAGED_MODELS", tmp_path)
    assert deps._missing_dep_kinds("large-v3-turbo") == []


def test_missing_kb_kinds_sqlite_vec_layer(monkeypatch):
    """§8C.13：sqlite-vec 软组件纳入缺失链；已就绪时不出现在清单"""
    monkeypatch.setattr(deps, "_find_llama_embed", lambda: None)
    monkeypatch.setattr(deps, "_find_model_file_embedding", lambda mid: None)
    monkeypatch.setattr(deps, "sqlite_vec_ready",
                        lambda force=False: (False, {"error": "t"}))
    assert deps._missing_kb_kinds("Qwen3-Embedding-0.6B") == [
        "llama-embed", "embedding:Qwen3-Embedding-0.6B", "sqlite-vec"]
    monkeypatch.setattr(deps, "sqlite_vec_ready",
                        lambda force=False: (True, {"version": "0.1.9"}))
    assert deps._missing_kb_kinds("Qwen3-Embedding-0.6B") == [
        "llama-embed", "embedding:Qwen3-Embedding-0.6B"]
