# -*- coding: utf-8 -*-
"""专用 Python 运行时 — smart-summarize v0.6.0（方案 docs/kb-sqlite-fts5-design-v1.4.md §8B）

技能与用户系统 Python 彻底解耦（用户决策 2026-09-06）：
- CPython 3.12 独立构建（python-build-standalone install_only tarball，SHA256 校验）
  装到 ~/.myagentrag/runtime/python/；
- venv（~/.myagentrag/runtime/venv/）安装 scripts/requirements-libs.txt 锁定的扩展库；
- 本模块可在"引导层"运行——引导层是任意 Python ≥3.8，**只允许标准库**
  （下载用 urllib、解压用 tarfile，禁 import requests/pip 库）；
- 不设置任何系统环境变量；路径全部由受管目录 MANAGED_HOME 进程内解析。

不回退到用户系统 Python（v1.4 决策 3）：运行时缺失 = 引导安装，而非退回旧链路。
"""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

from deps import MANAGED_HOME
import messages

RUNTIME_DIR = MANAGED_HOME / "runtime"
RUNTIME_PY_DIR = RUNTIME_DIR / "python"      # 独立 CPython（tarball 顶层目录名即 python/）
RUNTIME_VENV_DIR = RUNTIME_DIR / "venv"      # 扩展库 venv
RUNTIME_MANIFEST = RUNTIME_DIR / "manifest.json"

PYTHON_VERSION = "3.12.14"
PYTHON_BUILD_TAG = os.environ.get("MYAGENTRAG_PYTHON_BUILD_TAG") or os.environ.get("SMART_SUMMARIZE_PYTHON_BUILD_TAG", "20260901")
_PY_RELEASE_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{PYTHON_BUILD_TAG}")
_PY_MIRROR = os.environ.get("MYAGENTRAG_PYTHON_MIRROR") or os.environ.get("SMART_SUMMARIZE_PYTHON_MIRROR")  # 覆盖 Python 本体下载源

RUNTIME_REQUIREMENTS = Path(__file__).resolve().parent / "requirements-libs.txt"


# ==================== 路径与探测 ====================

def _triple():
    """python-build-standalone 的目标三元组"""
    if sys.platform == "win32":
        return ("aarch64-pc-windows-msvc"
                if platform.machine().upper() == "ARM64" else "x86_64-pc-windows-msvc")
    if sys.platform == "darwin":
        return ("aarch64-apple-darwin"
                if platform.machine() == "arm64" else "x86_64-apple-darwin")
    return ("aarch64-unknown-linux-gnu"
            if platform.machine() in ("aarch64", "arm64") else "x86_64-unknown-linux-gnu")


def _exe(dirpath, name="python"):
    exe = dirpath / (f"{name}.exe" if os.name == "nt" else name)
    return exe if exe.exists() and exe.is_file() else None


def runtime_python():
    """专用解释器（venv，含全部扩展库）；未安装返回 None"""
    return _exe(RUNTIME_VENV_DIR / ("Scripts" if os.name == "nt" else "bin"))


def venv_bin_dir():
    """专用 venv 的可执行目录（yt-dlp 等 console script 所在）；未安装返回 None"""
    d = RUNTIME_VENV_DIR / ("Scripts" if os.name == "nt" else "bin")
    return d if d.exists() else None


def find_runtime_python():
    return runtime_python()


def _same_exe(a, b):
    try:
        return os.path.normcase(Path(a).resolve()) == os.path.normcase(Path(b).resolve())
    except Exception:
        return False


def read_manifest():
    try:
        return json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_manifest(extra):
    data = {"created_at": datetime.now().isoformat(timespec="seconds"),
            "python_version": PYTHON_VERSION, "build_tag": PYTHON_BUILD_TAG,
            "triple": _triple()}
    data.update(extra)
    RUNTIME_MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")


# ==================== 下载（引导层仅标准库） ====================

def _download(url, dest, desc):
    """urllib 流式下载（自动走 HTTPS_PROXY 等系统代理环境变量），带进度"""
    import urllib.request
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "smart-summarize"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length", 0) or 0)
        done = 0
        while True:
            chunk = r.read(512 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  ⬇ {desc}: {done * 100 // total}% "
                      f"({done // 1048576}/{max(total // 1048576, 1)} MB)",
                      end="", flush=True, file=sys.stderr)
    print(file=sys.stderr, flush=True)
    return dest


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ==================== 安装 ====================

def install_runtime():
    """安装专用运行时：下载 CPython → 校验 → 解压 → 建 venv → 装锁定库 → 冒烟。
    返回专用解释器路径；失败抛异常（由调用方转结构化错误）。"""
    asset = (f"cpython-{PYTHON_VERSION}+{PYTHON_BUILD_TAG}"
             f"-{_triple()}-install_only_stripped.tar.gz")
    base_url = _PY_MIRROR or _PY_RELEASE_URL
    from urllib.parse import quote
    asset_q = quote(asset)  # GitHub release 路径要求 '+' 编码为 %2B
    print(f"  ⬇ 安装技能专用 Python 运行时（{PYTHON_VERSION}，独立于系统 Python）...",
          file=sys.stderr)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    dl = RUNTIME_DIR / ("dl_" + asset)
    try:
        _download(f"{base_url}/{asset_q}", dl, "CPython 运行时")
        # 校验：release 提供统一 SHA256SUMS 清单（无逐资产 sidecar）
        sums_dl = RUNTIME_DIR / "dl_SHA256SUMS"
        _download(f"{base_url}/SHA256SUMS", sums_dl, "SHA256SUMS")
        expected = None
        for line in sums_dl.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].strip() == asset:
                expected = parts[0].lower()
                break
        if not expected:
            raise RuntimeError(f"SHA256SUMS 中未找到 {asset} 的校验值")
        actual = _sha256_file(dl)
        if actual != expected:
            raise RuntimeError(f"SHA256 校验失败: {actual[:16]}… != {expected[:16]}…")

        # 解压（install_only tarball 顶层为 python/）
        if RUNTIME_PY_DIR.exists():
            shutil.rmtree(RUNTIME_PY_DIR, ignore_errors=True)
        try:
            with tarfile.open(dl, "r:gz") as tf:
                tf.extractall(RUNTIME_DIR, filter="data")
        except TypeError:  # Python <3.12 无 filter 参数
            with tarfile.open(dl, "r:gz") as tf:
                tf.extractall(RUNTIME_DIR)
        base_py = (_exe(RUNTIME_PY_DIR) if os.name == "nt"
                   else _exe(RUNTIME_PY_DIR / "bin", "python3"))
        if base_py is None:
            raise RuntimeError("解压后未找到 python 解释器")

        # venv + 锁定扩展库
        if RUNTIME_VENV_DIR.exists():
            shutil.rmtree(RUNTIME_VENV_DIR, ignore_errors=True)
        r = subprocess.run([str(base_py), "-m", "venv", str(RUNTIME_VENV_DIR)],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"venv 创建失败: {(r.stderr or '').strip()[-300:]}")
        vpy = runtime_python()
        print("  ⬇ 安装提取扩展库（requests/pdfplumber/pymupdf/python-docx/"
              "ebooklib/openpyxl/python-pptx/yt-dlp）...", file=sys.stderr)
        cmd = [str(vpy), "-m", "pip", "install", "-r", str(RUNTIME_REQUIREMENTS)]
        index_url = os.environ.get("MYAGENTRAG_PIP_INDEX_URL") or os.environ.get("SMART_SUMMARIZE_PIP_INDEX_URL")
        if index_url:
            cmd += ["--index-url", index_url]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"扩展库安装失败: {(r.stderr or '').strip()[-300:]}")

        # 冒烟：全部库可导入 + FTS5 trigram 可用（SS_SMOKE 哨兵行隔离第三方库的导入警告输出）
        smoke_code = (
            "import sqlite3, requests, pdfplumber, fitz, docx, ebooklib, openpyxl, pptx, yt_dlp;"
            "c = sqlite3.connect(':memory:');"
            "c.execute(\"CREATE VIRTUAL TABLE p USING fts5(x, tokenize='trigram')\");"
            "import sys; print('SS_SMOKE', sys.version.split()[0], sqlite3.sqlite_version)")
        r = subprocess.run([str(vpy), "-c", smoke_code],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"运行时冒烟失败: {(r.stderr or '').strip()[-300:]}")
        try:
            toks = next(l for l in (r.stdout or "").splitlines()
                        if l.startswith("SS_SMOKE")).split()
            py_ver, sqlite_ver = toks[1], toks[2]
        except (StopIteration, IndexError):
            raise RuntimeError(f"运行时冒烟输出异常: {(r.stdout or '')[-200:]}")
        _write_manifest({"python_build_version": py_ver, "sqlite_version": sqlite_ver})
        print(f"  ✅ 专用运行时就绪（Python {py_ver} / SQLite {sqlite_ver}）",
              file=sys.stderr)
        return vpy
    finally:
        for p in RUNTIME_DIR.glob("dl_*"):
            try:
                p.unlink()
            except OSError:
                pass


# ==================== 闸门（extract.py 启动时调用） ====================

def ensure_runtime(allow_install=False):
    """运行时闸门（v1.4 §8B：不回退用户系统 Python）。

    返回：
    - {"status": "ok-current"}  当前进程已是专用解释器
    - {"status": "ok-switch", "python": ...}  专用解释器已就绪，需 re-exec
    - {"status": "install-attempt", "python": ...}  本次引导完成安装，需 re-exec
    - {"status": "missing", ...}  不可用（allow_install=False 或安装失败）
    """
    rt = find_runtime_python()
    if rt is not None:
        if _same_exe(sys.executable, rt):
            return {"status": "ok-current"}
        return {"status": "ok-switch", "python": rt}
    if allow_install:
        try:
            vpy = install_runtime()
            return {"status": "install-attempt", "python": vpy}
        except Exception as e:
            return {"status": "missing", "install_attempted": True, "error": str(e)}
    return {"status": "missing", "install_attempted": False}
