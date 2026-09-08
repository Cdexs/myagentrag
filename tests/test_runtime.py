# -*- coding: utf-8 -*-
"""test_runtime — 专用 Python 运行时（方案 v1.4 §8B，离线部分）"""
import sys
from pathlib import Path

import pytest

import runtime


# ---------- 平台三元组 ----------

def test_triple_matrix(monkeypatch):
    cases = [
        ("win32", "AMD64", "x86_64-pc-windows-msvc"),
        ("win32", "ARM64", "aarch64-pc-windows-msvc"),
        ("darwin", "x86_64", "x86_64-apple-darwin"),
        ("darwin", "arm64", "aarch64-apple-darwin"),
        ("linux", "x86_64", "x86_64-unknown-linux-gnu"),
        ("linux", "aarch64", "aarch64-unknown-linux-gnu"),
    ]
    for plat, machine, expect in cases:
        monkeypatch.setattr(runtime.sys, "platform", plat)
        monkeypatch.setattr(runtime.platform, "machine", lambda m=machine: m)
        assert runtime._triple() == expect, (plat, machine)


def test_asset_url_built_from_triple(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "AMD64")
    monkeypatch.delenv("MYAGENTRAG_PYTHON_MIRROR", raising=False)
    asset = (f"cpython-{runtime.PYTHON_VERSION}+{runtime.PYTHON_BUILD_TAG}"
             f"-{runtime._triple()}-install_only_stripped.tar.gz")
    assert asset.startswith("cpython-3.12.")
    assert "x86_64-pc-windows-msvc" in asset
    assert runtime._PY_RELEASE_URL in (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        + runtime.PYTHON_BUILD_TAG)


# ---------- requirements-libs.txt ----------

def test_requirements_file_declares_all_libs():
    lines = [l.strip() for l in runtime.RUNTIME_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.strip().startswith("#")]
    names = {l.split(">=")[0].split("==")[0].strip().lower() for l in lines}
    assert names == {"requests", "yt-dlp", "pdfplumber", "pymupdf", "numpy",
                     "python-docx", "ebooklib", "openpyxl", "python-pptx"}
    # ebooklib 必须 >=0.20（常量命名空间变更后的受控下限）
    assert any(l.lower().startswith("ebooklib>=0.20") for l in lines)


# ---------- 探测与闸门 ----------

def test_find_runtime_python_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "RUNTIME_VENV_DIR", tmp_path / "nope")
    assert runtime.find_runtime_python() is None
    assert runtime.venv_bin_dir() is None


def test_find_runtime_python_present(tmp_path, monkeypatch):
    scripts = tmp_path / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
    scripts.mkdir(parents=True)
    exe = scripts / ("python.exe" if sys.platform == "win32" else "python")
    exe.write_bytes(b"")
    monkeypatch.setattr(runtime, "RUNTIME_VENV_DIR", tmp_path / "venv")
    assert runtime.find_runtime_python() == exe
    assert runtime.venv_bin_dir() == scripts


def test_ensure_runtime_ok_current(monkeypatch):
    monkeypatch.setattr(runtime, "find_runtime_python", lambda: Path(sys.executable))
    assert runtime.ensure_runtime()["status"] == "ok-current"


def test_ensure_runtime_switch(monkeypatch, tmp_path):
    fake = tmp_path / "other-python.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(runtime, "find_runtime_python", lambda: fake)
    r = runtime.ensure_runtime(allow_install=False)
    assert r["status"] == "ok-switch" and r["python"] == fake


def test_ensure_runtime_missing_no_install(monkeypatch):
    monkeypatch.setattr(runtime, "find_runtime_python", lambda: None)
    r = runtime.ensure_runtime(allow_install=False)
    assert r["status"] == "missing" and r.get("install_attempted") is False


def test_ensure_runtime_install_failure_structured(monkeypatch):
    """安装失败（如网络不可达）不得抛裸异常，返回结构化 missing"""
    monkeypatch.setattr(runtime, "find_runtime_python", lambda: None)

    def boom():
        raise RuntimeError("网络不可达")
    monkeypatch.setattr(runtime, "install_runtime", boom)
    r = runtime.ensure_runtime(allow_install=True)
    assert r["status"] == "missing" and r["install_attempted"] is True
    assert "网络不可达" in r["error"]


# ---------- 其他 ----------

def test_same_exe():
    assert runtime._same_exe(sys.executable, sys.executable) is True
    assert runtime._same_exe(sys.executable, "Z:\\definitely-other.exe") is False


def test_manifest_roundtrip(tmp_path, monkeypatch):
    mfile = tmp_path / "manifest.json"
    monkeypatch.setattr(runtime, "RUNTIME_MANIFEST", mfile)
    assert runtime.read_manifest() is None
    runtime._write_manifest({"sqlite_version": "3.50.4"})
    m = runtime.read_manifest()
    assert m["sqlite_version"] == "3.50.4" and m["python_version"] == runtime.PYTHON_VERSION
    assert "created_at" in m and m["triple"] == runtime._triple()
