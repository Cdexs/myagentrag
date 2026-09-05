# -*- coding: utf-8 -*-
"""test_deps — 组件定位 / 缺失检测 / 清单双语"""
import deps
import messages


def test_import_ok():
    assert deps._import_ok("json") is True
    assert deps._import_ok("no_such_module_xyz") is False


def test_missing_pipelib_kinds_present(monkeypatch):
    # requests 已装（测试环境必有）→ 无缺失
    assert deps._missing_pipelib_kinds(["requests"]) == []


def test_missing_pipelib_kinds_missing(monkeypatch):
    monkeypatch.setattr(deps, "_import_ok", lambda name: False)
    monkeypatch.setattr(deps.shutil, "which", lambda name: None)
    kinds = deps._missing_pipelib_kinds(["excel", "yt-dlp"])
    assert kinds == ["pip:excel", "pip:yt-dlp"]


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


def test_dep_detail_bilingual(tmp_path):
    messages.set_lang("zh")
    d = deps._dep_detail("pip:excel")
    assert d["purpose"] == "Excel (.xlsx/.xlsm) 表格文本提取"
    messages.set_lang("en")
    d = deps._dep_detail("pip:excel")
    assert d["purpose"] == "Excel (.xlsx/.xlsm) spreadsheet text extraction"
    messages.set_lang("zh")
    d = deps._dep_detail("model:large-v3-turbo")
    assert d["purpose"] == "whisper 转录模型 (large-v3-turbo)"
    assert d["source"].startswith("https://huggingface.co/")
    assert d["est_size"]  # 已知大小或实际大小


def test_cublas_assets_restored():
    # NVIDIA cublas 预编译链路曾因常量缺失 NameError（拆分前即存在），此为回归闸
    assert deps.WHISPERCPP_CUBLAS_ASSETS == [
        "whisper-cublas-11.8.0-bin-x64.zip",
        "whisper-cublas-12.4.0-bin-x64.zip",
    ]


def test_install_dep_unknown_kind():
    import pytest
    with pytest.raises(RuntimeError):
        deps._install_dep("nope:kind")


def test_epub_group_uses_ebooklib_import_name(monkeypatch):
    """回归（端侧验证发现，v0.5.x 起存量）：epub 组名≠导入名（ebooklib），
    未声明 import 时 _missing_pipelib_kinds 会 import 不存在的 "epub" 模块，
    导致已安装 ebooklib 仍永远误报缺失。"""
    real = deps._import_ok
    # 模拟：ebooklib 已安装、名为 epub 的模块不存在
    monkeypatch.setattr(deps, "_import_ok",
                        lambda name: True if name == "ebooklib" else real(name))
    assert deps._missing_pipelib_kinds(["epub"]) == []


def test_pip_groups_import_names_declared():
    """声明完整性：组名与实际导入名不一致的组必须显式声明 import 字段。"""
    assert deps.PIP_LIB_GROUPS["epub"]["import"] == "ebooklib"
    assert deps.PIP_LIB_GROUPS["docx"]["import"] == "docx"
    assert deps.PIP_LIB_GROUPS["excel"]["import"] == "openpyxl"
    assert deps.PIP_LIB_GROUPS["pptx"]["import"] == "pptx"
