# -*- coding: utf-8 -*-
"""test_deps — 组件定位 / 缺失检测 / 清单双语（v1.4：pip 组检测已随专用运行时移除）"""
from pathlib import Path

import pytest

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


# ---------- v0.1.2：ffmpeg 组件逐组件补齐（老装机升级补落 ffplay） ----------

def _fake_archive(names=("ffmpeg", "ffplay")):
    """构造与发行包同构的 fake zip 数据（含包名子目录）

    返回 (_http_download 替身, 调用记录)。替身把 zip 写入 dest（install_ffmpeg
    随后按 .zip 后缀解包），完整走真实解包/挑选/落盘链路。"""
    import io
    import zipfile
    calls = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            exe = n + (".exe" if deps.os.name == "nt" else "")
            zf.writestr(f"ffmpeg-release-essentials/bin/{exe}", f"fake-{n}")
    blob = buf.getvalue()

    def fake_download(url, dest, desc=""):
        calls.append(str(url))
        Path(dest).write_bytes(blob)
        return Path(dest)

    return fake_download, calls


@pytest.fixture
def _ffmpeg_env(tmp_path, monkeypatch):
    """受管 bin 指向临时目录 + 组件口径固定为 ffmpeg/ffplay（跨平台确定性）
    + 协议注册打桩（不写真实注册表）"""
    monkeypatch.setattr(deps, "MANAGED_BIN", tmp_path / "bin")
    (tmp_path / "bin").mkdir(parents=True)
    monkeypatch.setattr(deps, "_ffmpeg_component_names", lambda: ("ffmpeg", "ffplay"))
    monkeypatch.setattr(deps, "_ffmpeg_source_url",
                        lambda: "https://example.invalid/ffmpeg-release-essentials.zip")
    reg = []
    monkeypatch.setattr(deps, "_register_protocol_quietly", lambda: reg.append(1))
    monkeypatch.delenv("MYAGENTRAG_FFPLAY", raising=False)
    monkeypatch.setattr(deps.shutil, "which", lambda name: None)
    return {"reg": reg}


def test_install_ffmpeg_repairs_missing_ffplay(tmp_path, monkeypatch, _ffmpeg_env):
    """缺陷 1 回归：ffmpeg 已存在而 ffplay 缺失 → 只补 ffplay，不重拷 ffmpeg"""
    ff = deps._managed_bin_path("ffmpeg")
    ff.write_bytes(b"old-ffmpeg")
    fake_download, calls = _fake_archive()
    monkeypatch.setattr(deps, "_http_download", fake_download)
    out = deps.install_ffmpeg()
    assert out == ff
    assert ff.read_bytes() == b"old-ffmpeg"                     # 已有组件不覆盖
    assert deps._managed_bin_path("ffplay").read_text() == "fake-ffplay"
    assert calls                                                # 缺组件触发一次下载
    assert _ffmpeg_env["reg"] == [1]                            # 安装尾注册协议（不抛 NameError）


def test_install_ffmpeg_idempotent_when_complete(tmp_path, monkeypatch, _ffmpeg_env):
    """幂等：组件齐全 → 零下载直接返回"""
    for n in ("ffmpeg", "ffplay"):
        deps._managed_bin_path(n).write_bytes(b"x")

    def boom(*a, **kw):
        raise AssertionError("组件齐全时不应触发下载")

    monkeypatch.setattr(deps, "_http_download", boom)
    assert deps.install_ffmpeg() == deps._managed_bin_path("ffmpeg")
    assert _ffmpeg_env["reg"] == []                             # 无需修复时不触碰协议注册


def test_install_ffmpeg_component_absent_from_archive(tmp_path, monkeypatch, _ffmpeg_env, capsys):
    """发行包不含某组件（如 macOS evermeet 无 ffplay）：跳过并告警，不失败"""
    deps._managed_bin_path("ffmpeg").write_bytes(b"x")
    fake_download, _ = _fake_archive(names=("ffmpeg",))
    monkeypatch.setattr(deps, "_http_download", fake_download)
    assert deps.install_ffmpeg() == deps._managed_bin_path("ffmpeg")
    assert not deps._managed_bin_path("ffplay").exists()
    assert "ffplay" in capsys.readouterr().err


def test_ffmpeg_component_names_platform(monkeypatch):
    """平台口径：darwin 单体包只期望 ffmpeg；Windows/Linux 期望 ffmpeg+ffplay"""
    monkeypatch.setattr(deps.sys, "platform", "darwin")
    assert deps._ffmpeg_component_names() == ("ffmpeg",)
    monkeypatch.setattr(deps.sys, "platform", "win32")
    assert deps._ffmpeg_component_names() == ("ffmpeg", "ffplay")


def test_install_dep_ffmpeg_wired(monkeypatch):
    """接线回归：_install_dep('ffmpeg') 必须调到 install_ffmpeg（此前为未知组件）"""
    monkeypatch.setattr(deps, "install_ffmpeg", lambda: "FAKE_FFMPEG")
    assert deps._install_dep("ffmpeg") == "FAKE_FFMPEG"


def test_register_protocol_quietly_never_raises(monkeypatch, capsys):
    """安装尾协议注册：注册失败/异常都只告警，不影响安装结果"""
    import protocol
    monkeypatch.setattr(protocol, "register_protocol",
                        lambda: {"success": False, "error": "reg 挂了"})
    deps._register_protocol_quietly()
    assert "reg 挂了" in capsys.readouterr().err

    def boom():
        raise RuntimeError("protocol import 炸")

    monkeypatch.setattr(protocol, "register_protocol", boom)
    deps._register_protocol_quietly()                            # 不抛
    assert "protocol import 炸" in capsys.readouterr().err

    monkeypatch.setattr(protocol, "register_protocol",
                        lambda: {"success": True, "message": "已注册"})
    deps._register_protocol_quietly()
    assert "已注册" in capsys.readouterr().err


def test_repair_deps_reports_components(tmp_path, monkeypatch, _ffmpeg_env):
    """--repair-deps：补齐后 repaired 如实列出新组件，ffplay 标注来源"""
    def fake_install():
        for n in ("ffmpeg", "ffplay"):
            deps._managed_bin_path(n).write_bytes(b"x")
        return deps._managed_bin_path("ffmpeg")

    monkeypatch.setattr(deps, "install_ffmpeg", fake_install)
    r = deps.repair_deps()
    assert r["success"] is True and r["action"] == "repair_deps"
    assert r["repaired"] == ["ffmpeg", "ffplay"]
    assert all(s["present"] for s in r["components"].values())
    assert r["ffplay"]["available"] is True and r["ffplay"]["source"] == "managed"
    assert "ffmpeg" in r["message"]


def test_repair_deps_none_needed_and_failure(tmp_path, monkeypatch, _ffmpeg_env):
    """全齐 → 零修复；安装抛错 → 结构化失败 + 指引（不裸抛）"""
    for n in ("ffmpeg", "ffplay"):
        deps._managed_bin_path(n).write_bytes(b"x")
    monkeypatch.setattr(deps, "install_ffmpeg",
                        lambda: deps._managed_bin_path("ffmpeg"))
    r = deps.repair_deps()
    assert r["success"] is True and r["repaired"] == []

    def boom():
        raise RuntimeError("网络不可达")

    monkeypatch.setattr(deps, "install_ffmpeg", boom)
    monkeypatch.setattr(deps, "_ffplay_source", lambda: None)
    r2 = deps.repair_deps()
    assert r2["success"] is False and "网络不可达" in r2["error"]
    assert r2["hint"] and "repair-deps" in r2["hint"]
