# -*- coding: utf-8 -*-
"""运行时依赖体系

组件定位、缺失检测与确认安装（专用 Python 运行时 / ffmpeg / whisper-cli / ggml 模型）：
缺失时列出清单（名称/用途/来源/预计大小），经用户确认后下载安装并继续原任务。

v1.4（方案 §8B）：扩展库随专用运行时预装（scripts/requirements-libs.txt），
不再检测用户环境、不再向用户解释器 pip 安装任何库；pip 组检测逻辑已移除。

依赖方向：slicing ← deps ← transcribe ← extract；runtime ← deps（惰性互引）；
extractors 独立。
"""
import os
import platform
import shutil
import sqlite3  # v0.7.1 §8C.13：sqlite-vec 探测冒烟用
import subprocess
import sys
import tempfile
from pathlib import Path

# 受管组件根目录（可用 MYAGENTRAG_HOME 覆盖）
MANAGED_HOME = Path(
    os.environ.get("MYAGENTRAG_HOME") or str(Path.home() / ".myagentrag")
).expanduser()
MANAGED_BIN = MANAGED_HOME / "bin"
MANAGED_MODELS = MANAGED_HOME / "models"


def _default_whispercpp_dir():
    return MANAGED_BIN


def _default_whispercpp_models_dir():
    return MANAGED_MODELS


WHISPERCPP_DIR = Path(os.environ.get(
    "MYAGENTRAG_WHISPERCPP_DIR",
    str(_default_whispercpp_dir()),
)).expanduser()
WHISPERCPP_MODELS_DIR = Path(os.environ.get(
    "MYAGENTRAG_WHISPERCPP_MODELS_DIR",
    str(_default_whispercpp_models_dir()),
)).expanduser()

# faster-whisper 风格模型名 -> whisper.cpp ggml 模型文件
WHISPERCPP_GGML_MAP = {
    "large-v3-turbo": "ggml-large-v3-turbo.bin",          # fp16，参考精度（默认）
    "large-v3-turbo-q5_0": "ggml-large-v3-turbo-q5_0.bin",  # 量化快速档
}

# ==================== 组件定位（环境变量 → PATH → 受管目录） ====================

def _find_ffmpeg():
    configured = os.environ.get("MYAGENTRAG_FFMPEG")
    if configured:
        p = Path(configured).expanduser()
        if p.exists() and p.is_file():
            return str(p)
    p = shutil.which("ffmpeg")
    if p:
        return p
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    managed = MANAGED_BIN / exe
    if managed.exists() and managed.is_file():
        return str(managed)
    return None


def _find_whispercpp_cli():
    configured = os.environ.get("MYAGENTRAG_WHISPERCPP_CLI")
    if configured:
        p = Path(configured).expanduser()
        if p.exists() and p.is_file():
            return p

    # 先查 PATH，便于 macOS/Linux 通过包管理器或自行安装后直接使用。
    path_cli = shutil.which("whisper-cli")
    if path_cli:
        return Path(path_cli)

    names = ("whisper-cli.exe", "whisper-cli") if os.name == "nt" else ("whisper-cli", "whisper-cli.exe")
    for name in names:
        for base in (WHISPERCPP_DIR, MANAGED_BIN):
            candidate = base / name
            if candidate.exists() and candidate.is_file():
                return candidate
    return None


def _model_candidates(model_name):
    """该模型所有可能的位置（按查找/下载优先级）"""
    fname = WHISPERCPP_GGML_MAP.get(model_name, f"ggml-{model_name}.bin")
    dirs = []
    for d in (WHISPERCPP_MODELS_DIR, MANAGED_MODELS):
        d = Path(d).expanduser()
        if d not in dirs:
            dirs.append(d)
    return [d / fname for d in dirs]


def _find_model_file(model_name):
    for p in _model_candidates(model_name):
        if p.exists() and p.is_file():
            return p
    return None


def _whispercpp_available(model_name):
    cli = _find_whispercpp_cli()
    return cli is not None and _find_model_file(model_name) is not None


# ==================== 运行时依赖检测与确认下载 ====================

class MissingDependencyError(Exception):
    """所需组件缺失；kinds: 'runtime' / 'ffmpeg' / 'whisper-cli' / 'model:<name>'"""
    def __init__(self, kinds):
        self.kinds = kinds
        super().__init__("缺少组件: " + ", ".join(kinds))

MODEL_URL_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
WHISPERCPP_REPO_URL = "https://github.com/ggml-org/whisper.cpp"
# HEAD 拿不到实际大小时的回退估计值（字节）
KNOWN_MODEL_SIZES = {
    "ggml-large-v3-turbo.bin": 1_620_000_000,
    "ggml-large-v3-turbo-q5_0.bin": 574_000_000,
}

def _human_size(n):
    if not n or n <= 0:
        return "未知大小"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024

def _content_length(url):
    try:
        import requests
        r = requests.head(url, allow_redirects=True, timeout=30)
        return int(r.headers.get("Content-Length", 0) or 0)
    except Exception:
        return 0

def _http_download(url, dest, desc=""):
    """流式下载到 dest（先写 .part），带进度显示，返回最终路径"""
    import requests
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0) or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=512 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  ⬇ {desc}: {done * 100 // total}% ({_human_size(done)}/{_human_size(total)})",
                          end="", flush=True, file=sys.stderr)
        print(file=sys.stderr, flush=True)
    if dest.exists():
        dest.unlink()
    tmp.rename(dest)
    return dest

def _ffmpeg_source_url():
    if os.name == "nt":
        return "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    if sys.platform == "darwin":
        return "https://evermeet.cx/ffmpeg/getrelease/zip"
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
    if machine in ("aarch64", "arm64"):
        return "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz"
    return None

def install_ffmpeg():
    """用户确认后安装 ffmpeg 到受管 bin 目录，返回可执行文件路径"""
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    dest = MANAGED_BIN / exe
    if dest.exists():
        return dest
    url = _ffmpeg_source_url()
    if not url:
        raise RuntimeError("不支持的架构，请用系统包管理器安装 ffmpeg (apt/dnf/pacman/brew)")
    MANAGED_BIN.mkdir(parents=True, exist_ok=True)
    import zipfile
    import tarfile
    with tempfile.TemporaryDirectory(prefix="myag_ffmpeg_dl_") as td:
        archive = _http_download(url, Path(td) / url.split("/")[-1].split("?")[0], "ffmpeg")
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(td)
        else:
            with tarfile.open(archive) as tf:
                tf.extractall(td)
        found = None
        for p in Path(td).rglob(exe):
            if p.is_file() and p.name == Path(exe).stem + (".exe" if os.name == "nt" else ""):
                found = p
                break
        if not found:
            raise RuntimeError("下载包中未找到 ffmpeg 可执行文件")
        shutil.copy2(found, dest)
    if os.name != "nt":
        dest.chmod(0o755)
    return dest

WHISPERCPP_PREBUILT_ASSETS = {
    ("nt", "AMD64"): "whisper-bin-x64.zip",
    ("nt", "ARM64"): "whisper-bin-arm64.zip",
}
# NVIDIA 官方 cublas 预编译版（自带 CUDA 运行库，无需安装 CUDA Toolkit）；
# 11.8.0 兼容老驱动（270MB），12.4.0 覆盖新卡（671MB），按优先级尝试
WHISPERCPP_CUBLAS_ASSETS = [
    "whisper-cublas-11.8.0-bin-x64.zip",
    "whisper-cublas-12.4.0-bin-x64.zip",
]

def _detect_gpu():
    """检测本机 GPU 厂商，返回 (vendor, device_desc, gpu_hint)。"""
    if sys.platform == "darwin":
        return ("apple", "Apple Silicon / Metal", "macOS 构建默认启用 Metal，无需额外操作")
    if shutil.which("nvidia-smi"):
        name = ""
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=20)
            name = (r.stdout or "").strip().splitlines()[0] if r.stdout.strip() else ""
        except Exception:
            pass
        return ("nvidia", name or "NVIDIA GPU", "官方 cublas 预编译版可用（自带 CUDA 运行库，无需 CUDA Toolkit）")
    if os.name == "nt":
        names = ""
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "(Get-CimInstance Win32_VideoController).Name -join '; '"],
                               capture_output=True, text=True, timeout=40)
            names = (r.stdout or "").strip()
        except Exception:
            pass
        low = names.lower()
        if "radeon" in low or "amd" in low:
            return ("amd", names, "官方无 A 卡 GPU 预编译；安装 Vulkan SDK 后可源码构建 Vulkan 版")
        if "intel" in low:
            return ("intel", names, "安装 Vulkan SDK 后可源码构建 Vulkan 版")
        return ("cpu", names or "未知", "")
    # Linux / WSL
    names = ""
    try:
        if shutil.which("lspci"):
            r = subprocess.run(["lspci"], capture_output=True, text=True, timeout=20)
            vga = [l.split(":", 2)[-1].strip() for l in (r.stdout or "").splitlines()
                   if "vga" in l.lower() or "display" in l.lower()]
            names = "; ".join(vga)
    except Exception:
        pass
    low = names.lower()
    if "nvidia" in low or shutil.which("nvidia-smi"):
        return ("nvidia", names, "安装 CUDA Toolkit 后源码构建，或使用官方 main-cuda Docker 镜像")
    if "amd" in low or "radeon" in low:
        return ("amd", names, "独显可装 ROCm 后源码构建 HIP 版；核显/无 ROCm 时装 Vulkan SDK 构建 Vulkan 版")
    if "intel" in low:
        return ("intel", names, "安装 Vulkan SDK 后可源码构建 Vulkan 版")
    return ("cpu", names or "未知", "")

def install_whispercli():
    """用户确认后安装 whisper.cpp：优先包管理器/官方预编译，源码构建兜底。"""
    exe = "whisper-cli.exe" if os.name == "nt" else "whisper-cli"
    dest = MANAGED_BIN / exe
    if dest.exists():
        return dest
    if shutil.which("whisper-cli"):
        return Path(shutil.which("whisper-cli"))

    # 1) 包管理器（macOS/Linux 有 Homebrew 时最省事）
    if shutil.which("brew"):
        print("  ⬇ 尝试 brew install whisper-cpp（预编译包）...", file=sys.stderr)
        r = subprocess.run(["brew", "install", "whisper-cpp"],
                           capture_output=True, text=True, timeout=3600)
        if r.returncode == 0 and shutil.which("whisper-cli"):
            return Path(shutil.which("whisper-cli"))
        print("  ⚠️ brew 安装未成功，改用其他方式", file=sys.stderr)

    # 2) Windows：官方预编译版（NVIDIA → cublas GPU 版；其他 → CPU 版）
    gpu_vendor, gpu_name, gpu_hint = _detect_gpu()
    prebuilt_assets = []
    if os.name == "nt" and gpu_vendor == "nvidia":
        prebuilt_assets += WHISPERCPP_CUBLAS_ASSETS
    prebuilt_assets += [WHISPERCPP_PREBUILT_ASSETS.get((os.name, platform.machine().upper()))]
    for asset in [a for a in prebuilt_assets if a]:
        url = f"https://github.com/ggml-org/whisper.cpp/releases/latest/download/{asset}"
        try:
            MANAGED_BIN.mkdir(parents=True, exist_ok=True)
            import zipfile
            with tempfile.TemporaryDirectory(prefix="myag_wcpp_dl_") as td:
                archive = _http_download(url, Path(td) / asset, asset)
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(td)
                built = None
                for p in Path(td).rglob(exe):
                    if p.is_file():
                        built = p
                        break
                if built:
                    shutil.copy2(built, dest)
                    # 官方预编译 exe 依赖同目录的 DLL（whisper.dll/ggml.dll 等），必须一起拷
                    for extra in built.parent.iterdir():
                        if extra.is_file() and extra.suffix.lower() in (".dll", ".so"):
                            shutil.copy2(extra, dest.parent)
                            print(f"  + DLL: {extra.name}", file=sys.stderr)
                    if asset.startswith("whisper-cublas"):
                        print(f"  🎮 已安装 NVIDIA cublas GPU 版（自带 CUDA 运行库，无需 CUDA Toolkit）", file=sys.stderr)
                    elif gpu_vendor == "amd":
                        print(f"  ⚠️ 已安装 CPU 版。检测到 AMD GPU（{gpu_name}）但官方无 A 卡 GPU 预编译；"
                              "如需 GPU 加速：安装 Vulkan SDK 后删除受管二进制重跑（将源码构建 Vulkan 版），"
                              "或用 MYAGENTRAG_WHISPERCPP_CLI 指向已有的 GPU 构建", file=sys.stderr)
                    elif gpu_vendor in ("intel",):
                        print(f"  ⚠️ 已安装 CPU 版。检测到 Intel GPU（{gpu_name}）；"
                              "安装 Vulkan SDK 后删除受管二进制重跑可构建 Vulkan GPU 版", file=sys.stderr)
                    return dest
        except Exception as e:
            print(f"  ⚠️ 官方预编译包 {asset} 下载失败（{e}），尝试下一个方式", file=sys.stderr)

    # 3) 源码构建兜底（需要 git/cmake/编译器）
    for tool in ("git", "cmake"):
        if not shutil.which(tool):
            raise RuntimeError(f"源码构建需要 {tool}，请先安装后重试，或手动安装 whisper.cpp（如 brew install whisper-cpp）")
    MANAGED_HOME.mkdir(parents=True, exist_ok=True)
    repo_dir = MANAGED_HOME / "whisper.cpp"
    if not (repo_dir / ".git").exists():
        print(f"  ⬇ 克隆 whisper.cpp（浅克隆，约 60MB）...", file=sys.stderr)
        r = subprocess.run(["git", "clone", "--depth", "1", WHISPERCPP_REPO_URL, str(repo_dir)],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"git clone 失败: {(r.stderr or '').strip()[-300:]}")
    build_dir = repo_dir / "build"

    # GPU 后端自动检测：按硬件厂商与工具链选择后端，没有则明确告知走 CPU。
    # macOS 无需处理：ggml CMake 默认启用 Metal。
    gpu_vendor, gpu_name, gpu_hint = _detect_gpu()
    gpu_flags = []
    if gpu_vendor == "apple":
        print("  🔧 macOS：CMake 默认启用 Metal GPU 加速", file=sys.stderr)
    else:
        has_nvcc = bool(shutil.which("nvcc"))
        if gpu_vendor == "nvidia":
            if has_nvcc:
                gpu_flags += ["-DGGML_CUDA=ON"]
                print(f"  🎮 检测到 NVIDIA GPU（{gpu_name}）+ CUDA Toolkit，启用 CUDA 后端", file=sys.stderr)
            else:
                print("  ⚠️ 检测到 NVIDIA GPU 但未安装 CUDA Toolkit (nvcc)，本次构建为 CPU 版；"
                      "如需 GPU 加速请安装 CUDA Toolkit 后删除构建目录重试", file=sys.stderr)
        elif gpu_vendor == "amd" and shutil.which("rocminfo"):
            # A 卡独显走 ROCm/HIP；核显（APU）ROCm 不支持，用户可自行改用 Vulkan
            try:
                r = subprocess.run(["rocminfo"], capture_output=True, text=True, timeout=30)
                gfx = next((l.strip().split(":")[-1].strip()
                            for l in (r.stdout or "").splitlines() if "gfx" in l.lower()), "")
            except Exception:
                gfx = ""
            targets = [t for t in (gfx, "gfx1100", "gfx1201") if t]
            gpu_flags += ["-DGGML_HIP=ON", f"-DAMDGPU_TARGETS={';'.join(dict.fromkeys(targets))}"]
            print(f"  🎮 检测到 AMD GPU（{gpu_name or gfx}），启用 HIP/ROCm 后端（目标: {targets[0]}）", file=sys.stderr)
        else:
            # VULKAN_SDK 环境变量 / glslc（SDK 自带）才代表真正装了 SDK；
            # System32 里的 vulkaninfo 只是驱动附带的运行时工具，不能用来编译。
            has_vulkan = bool(os.environ.get("VULKAN_SDK")) or bool(shutil.which("glslc"))
            if has_vulkan:
                gpu_flags += ["-DGGML_VULKAN=ON"]
                print(f"  🎮 检测到 Vulkan SDK，启用 Vulkan 后端（适用于 {gpu_name or '所有支持 Vulkan 的 GPU'}）", file=sys.stderr)
            else:
                hint = f"（{gpu_hint}）" if gpu_hint else ""
                print(f"  ⚠️ 未检测到可用 GPU 工具链，本次构建为 CPU 版{hint}；"
                      "如需 GPU 加速请安装对应 SDK（NVIDIA: CUDA Toolkit / AMD-Intel: Vulkan SDK）"
                      "后删除构建目录重试", file=sys.stderr)
    custom_flags = os.environ.get("MYAGENTRAG_WHISPERCPP_CMAKE_FLAGS", "").split()
    cmake_args = ["cmake", "-S", str(repo_dir), "-B", str(build_dir),
                  "-DCMAKE_BUILD_TYPE=Release", "-DWHISPER_BUILD_TESTS=OFF"] + gpu_flags + custom_flags
    r = subprocess.run(cmake_args, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"cmake 配置失败: {(r.stderr or '').strip()[-300:]}")
    print("  🔧 编译 whisper-cli（可能需要数分钟）...", file=sys.stderr)
    r = subprocess.run(["cmake", "--build", str(build_dir), "--config", "Release",
                        "--target", "whisper-cli", "--parallel"],
                       capture_output=True, text=True, timeout=7200)
    if r.returncode != 0:
        raise RuntimeError(f"编译失败: {(r.stderr or '').strip()[-300:]}")
    built = None
    for p in build_dir.rglob(exe):
        if p.is_file():
            built = p
            break
    if not built:
        raise RuntimeError("编译完成但未找到 whisper-cli 二进制")
    MANAGED_BIN.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, dest)
    if os.name != "nt":
        dest.chmod(0o755)
    return dest

def _model_download_dir():
    configured = os.environ.get("MYAGENTRAG_WHISPERCPP_MODELS_DIR")
    if configured:
        return Path(configured).expanduser()
    return MANAGED_MODELS

def install_model(model_name):
    """用户确认后从 HuggingFace 下载 ggml 模型，返回模型文件路径"""
    fname = WHISPERCPP_GGML_MAP.get(model_name, f"ggml-{model_name}.bin")
    # 与 _find_model_file 同口径：模型在任一已知位置（环境变量目录/受管目录）已存在
    # 即直接返回，避免对同一模型重复下载 1.5GB（端侧审计发现：环境变量指向的目录
    # 无模型而受管目录有时，旧逻辑会无视已有副本整包重下）
    found = _find_model_file(model_name)
    if found:
        return found
    dest = _model_download_dir() / fname
    return _http_download(MODEL_URL_BASE + fname, dest, fname)

def _missing_dep_kinds(model_name):
    kinds = []
    if not _find_ffmpeg():
        kinds.append("ffmpeg")
    if not _find_whispercpp_cli():
        kinds.append("whisper-cli")
    if not _find_model_file(model_name):
        kinds.append(f"model:{model_name}")
    return kinds

def _dep_detail(kind):
    import messages
    if kind == "runtime":
        return {"kind": kind, "name": "Python 专用运行时 (MyAgentRAG runtime)",
                "purpose": messages.msg("dep_purpose_runtime"),
                "source": messages.msg("dep_source_runtime"),
                "est_size": messages.msg("dep_size_runtime")}
    if kind == "llama-embed":
        return {"kind": kind, "name": "llama.cpp 嵌入引擎",
                "purpose": messages.msg("dep_purpose_llama_embed"),
                "source": messages.msg("dep_source_llama_embed", tag=LLAMA_CPP_RELEASE),
                "est_size": "约 18–34MB"}
    if kind == "sqlite-vec":
        return {"kind": kind, "name": "sqlite-vec 向量检索扩展",
                "purpose": messages.msg("dep_purpose_sqlite_vec"),
                "source": messages.msg("dep_source_sqlite_vec"),
                "est_size": "约 0.3MB"}
    if kind.startswith("embedding:"):
        model_id = kind.split(":", 1)[1]
        spec = EMBEDDING_MODELS.get(model_id, {})
        return {"kind": kind, "name": model_id,
                "purpose": messages.msg("dep_purpose_embedding_model", model=model_id),
                "source": messages.msg("dep_source_embedding_model", model=model_id),
                "est_size": _human_size(spec.get("size_bytes", 0)) or "未知大小"}
    if kind == "ffmpeg":
        url = _ffmpeg_source_url()
        size = _content_length(url) if url else 0
        return {"kind": kind, "name": "ffmpeg",
                "purpose": messages.msg("dep_purpose_ffmpeg"),
                "source": url or "系统包管理器（apt/dnf/pacman/brew）",
                "est_size": _human_size(size) if size else messages.msg("dep_size_ffmpeg_est")}
    if kind == "whisper-cli":
        return {"kind": kind, "name": "whisper-cli (whisper.cpp)",
                "purpose": messages.msg("dep_purpose_whispercli"),
                "source": messages.msg("dep_source_whispercli"),
                "est_size": messages.msg("dep_size_whispercli")}
    model_name = kind.split(":", 1)[1]
    fname = WHISPERCPP_GGML_MAP.get(model_name, f"ggml-{model_name}.bin")
    size = _content_length(MODEL_URL_BASE + fname) or KNOWN_MODEL_SIZES.get(fname, 0)
    return {"kind": kind, "name": fname,
            "purpose": messages.msg("dep_purpose_model", model=model_name),
            "source": MODEL_URL_BASE + fname,
            "est_size": _human_size(size)}

def _install_dep(kind):
    if kind == "runtime":
        # 惰性导入避免 deps↔runtime 循环（runtime 顶部 import deps）
        import runtime
        return runtime.install_runtime()
    if kind == "llama-embed":
        return install_llama_embed()
    if kind == "sqlite-vec":
        return install_sqlite_vec()
    if kind.startswith("embedding:"):
        return install_embedding_model(kind.split(":", 1)[1])
    if kind == "whisper-cli":
        return install_whispercli()
    if kind.startswith("model:"):
        return install_model(kind.split(":", 1)[1])
    raise RuntimeError(f"未知组件类型: {kind}")


# ==================== llama.cpp 嵌入引擎 + 向量模型（方案 v1.5 §8C） ====================

LLAMA_CPP_RELEASE = os.environ.get("MYAGENTRAG_LLAMA_CPP_RELEASE", "b10819")
HF_BASE = os.environ.get("MYAGENTRAG_HF_MIRROR", "https://huggingface.co")

# 向量模型注册表（默认 Qwen3，用户决策 2026-09-06；bge-m3/e5-small 为注册表备选档）
EMBEDDING_MODELS = {
    "Qwen3-Embedding-0.6B": {
        "gguf_repo": "Qwen/Qwen3-Embedding-0.6B-GGUF",
        "gguf_file": "Qwen3-Embedding-0.6B-Q8_0.gguf",
        "size_bytes": 639_629_312,
        "dims": 1024, "license": "apache-2.0", "default": True,
    },
}

LLAMA_EMBED_SUBDIR = "llama"  # 受管 bin/llama/：与 whisper-cli 隔离（ggml*.dll 混放会致后端扫描崩溃）


def _find_llama_embed():
    """定位 llama.cpp 嵌入引擎（llama-server，/v1/embeddings 端点）：
    环境变量 → 受管 bin/llama/ → PATH。新版发布包不再含独立 llama-embedding exe。"""
    configured = os.environ.get("MYAGENTRAG_LLAMA_EMBED")
    if configured:
        p = Path(configured).expanduser()
        if p.exists() and p.is_file():
            return p
    exe = "llama-server.exe" if os.name == "nt" else "llama-server"
    managed = MANAGED_BIN / LLAMA_EMBED_SUBDIR / exe
    if managed.exists() and managed.is_file():
        return managed
    return shutil.which(exe)


def _llama_embed_asset():
    arch = "arm64" if platform.machine().upper() in ("ARM64", "AARCH64") else "x64"
    return f"llama-{LLAMA_CPP_RELEASE}-bin-win-cpu-{arch}.zip"


def install_llama_embed():
    """安装 llama.cpp 嵌入引擎。**GPU 优先**（默认下载 GPU 加速包），不可用回退 CPU：
    - Windows：检测到 GPU（NVIDIA/AMD/Intel）→ Vulkan 版（34MB，三大厂商通吃）→ 失败回退 CPU 版；ARM64 无 Vulkan 资产 → CPU 版
    - macOS：brew / 源码构建（Metal GPU 默认启用）
    - Linux/WSL：源码构建——NVIDIA+nvcc → CUDA；Vulkan SDK → Vulkan；否则 CPU（如实告知）
    装到受管 bin/llama/ 子目录（与 whisper-cli 的 ggml DLL 隔离，避免后端扫描冲突）。"""
    found = _find_llama_embed()
    if found:
        return found

    def _extract_win_zip(asset):
        url = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_RELEASE}/{asset}"
        target = MANAGED_BIN / LLAMA_EMBED_SUBDIR
        target.mkdir(parents=True, exist_ok=True)
        import zipfile
        with tempfile.TemporaryDirectory(prefix="myag_llama_dl_") as td:
            archive = _http_download(url, Path(td) / asset, "llama.cpp 嵌入引擎")
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(td)
            for name in ("llama-server",):
                exe = name + (".exe" if os.name == "nt" else "")
                built = next((q for q in Path(td).rglob(exe) if q.is_file()), None)
                if not built:
                    return None
                shutil.copy2(built, target / exe)
            # 运行所需 DLL 一并拷入 llama/ 子目录（不进 bin/ 根，避免污染 whisper-cli）
            for extra in Path(td).rglob("*.dll"):
                shutil.copy2(extra, target / extra.name)
        return target / ("llama-server.exe" if os.name == "nt" else "llama-server")

    # 1) Windows：GPU 优先，CPU 兜底
    if os.name == "nt":
        machine = platform.machine().upper()
        if machine in ("ARM64", "AARCH64"):
            # 官方无 vulkan-arm64 资产 → CPU 版
            out = _extract_win_zip(f"llama-{LLAMA_CPP_RELEASE}-bin-win-cpu-arm64.zip")
            if not out:
                raise RuntimeError("Windows ARM64 嵌入引擎下载失败")
            return out
        vendor, gpu_name, _ = _detect_gpu()
        gpu_capable = vendor in ("nvidia", "amd", "intel")
        if gpu_capable:
            try:
                out = _extract_win_zip(f"llama-{LLAMA_CPP_RELEASE}-bin-win-vulkan-x64.zip")
                if out:
                    print(f"  🎮 已安装 Vulkan GPU 加速版（{gpu_name}）", file=sys.stderr)
                    return out
            except Exception as e:
                print(f"  ⚠️ Vulkan 包下载失败（{e}），回退 CPU 版", file=sys.stderr)
        out = _extract_win_zip(f"llama-{LLAMA_CPP_RELEASE}-bin-win-cpu-x64.zip")
        if not out:
            raise RuntimeError("llama.cpp 嵌入引擎下载失败（Vulkan 与 CPU 版均失败）")
        if gpu_capable:
            print("  ⚠️ Vulkan 版不可用，已回退 CPU 版（无 GPU 加速）", file=sys.stderr)
        return out
    # 2) macOS：brew install llama.cpp（Metal GPU 默认启用）
    if sys.platform == "darwin" and shutil.which("brew"):
        print("  ⬇ 尝试 brew install llama.cpp（预编译包，Metal GPU 默认启用）...", file=sys.stderr)
        r = subprocess.run(["brew", "install", "llama.cpp"],
                           capture_output=True, text=True, timeout=3600)
        if r.returncode == 0 and _find_llama_embed():
            return _find_llama_embed()
        print("  ⚠️ brew 安装未成功，改用源码构建", file=sys.stderr)
    # 3) Linux/WSL/兜底：源码构建，按工具链自动选 GPU 后端
    for tool in ("git", "cmake"):
        if not shutil.which(tool):
            raise RuntimeError(f"源码构建需要 {tool}，请先安装后重试")
    repo_dir = MANAGED_HOME / "llama.cpp"
    if not (repo_dir / ".git").exists():
        print("  ⬇ 克隆 llama.cpp（浅克隆）...", file=sys.stderr)
        r = subprocess.run(["git", "clone", "--depth", "1",
                            "https://github.com/ggml-org/llama.cpp", str(repo_dir)],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"git clone 失败: {(r.stderr or '').strip()[-300:]}")
    build_dir = repo_dir / "build"
    gpu_flags = []
    vendor, gpu_name, _ = _detect_gpu()
    if shutil.which("nvcc") and vendor == "nvidia":
        gpu_flags = ["-DGGML_CUDA=ON"]
        print(f"  🎮 检测到 NVIDIA GPU（{gpu_name}）+ CUDA Toolkit，启用 CUDA 后端", file=sys.stderr)
    elif os.environ.get("VULKAN_SDK") or shutil.which("glslc"):
        gpu_flags = ["-DGGML_VULKAN=ON"]
        print(f"  🎮 检测到 Vulkan SDK，启用 Vulkan 后端", file=sys.stderr)
    else:
        print("  ⚠️ 未检测到 GPU 工具链（CUDA Toolkit / Vulkan SDK），本次构建为 CPU 版", file=sys.stderr)
    r = subprocess.run(["cmake", "-S", str(repo_dir), "-B", str(build_dir),
                        "-DCMAKE_BUILD_TYPE=Release", "-DGGML_NATIVE=OFF"] + gpu_flags,
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"cmake 配置失败: {(r.stderr or '').strip()[-300:]}")
    print("  🔧 编译 llama-embedding / llama-server（可能需要数分钟）...", file=sys.stderr)
    r = subprocess.run(["cmake", "--build", str(build_dir), "--config", "Release",
                        "--target", "llama-embedding", "llama-server", "--parallel"],
                       capture_output=True, text=True, timeout=7200)
    if r.returncode != 0:
        raise RuntimeError(f"编译失败: {(r.stderr or '').strip()[-300:]}")
    target = MANAGED_BIN / LLAMA_EMBED_SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    for name in ("llama-server",):
        built = next((q for q in build_dir.rglob(name) if q.is_file()), None)
        if built:
            shutil.copy2(built, target / name)
            (target / name).chmod(0o755)
    out = _find_llama_embed()
    if not out:
        raise RuntimeError("编译完成但未找到 llama-server")
    return out


def install_embedding_model(model_id):
    """下载向量模型（GGUF）到受管 models/embedding/<id>/；已存在即返回"""
    spec = EMBEDDING_MODELS.get(model_id)
    if not spec:
        raise RuntimeError(f"未知向量模型: {model_id}")
    dest = MANAGED_MODELS / "embedding" / model_id / spec["gguf_file"]
    if dest.exists() and dest.is_file():
        return dest
    url = f"{HF_BASE}/{spec['gguf_repo']}/resolve/main/{spec['gguf_file']}"
    return _http_download(url, dest, model_id)


def _missing_kb_kinds(model_id):
    """知识库依赖链缺失判定（方案 §8C.11；运行时由 extract.py 运行时闸门单独处理）。
    sqlite-vec 为软组件：缺失列入清单，但安装/加载失败由调用方回退 numpy（§8C.13）。"""
    kinds = []
    if not _find_llama_embed():
        kinds.append("llama-embed")
    if not _find_model_file_embedding(model_id):
        kinds.append(f"embedding:{model_id}")
    if not sqlite_vec_ready()[0]:
        kinds.append("sqlite-vec")
    return kinds


def _find_model_file_embedding(model_id):
    spec = EMBEDDING_MODELS.get(model_id)
    if not spec:
        return None
    p = MANAGED_MODELS / "embedding" / model_id / spec["gguf_file"]


# ==================== sqlite-vec 向量后端（方案 v1.5 §8C.13，v0.7.1） ====================

SQLITE_VEC_PACKAGE = "sqlite-vec>=0.1.9"
_SQLITE_VEC_STATE = None  # (ok, info) 进程内缓存


def _sqlite_vec_loadable_path():
    """GitHub tarball 兜底安装后的扩展文件位置（受管 bin/sqlite-vec/）"""
    name = "vec0.dll" if os.name == "nt" else ("vec0.dylib" if sys.platform == "darwin" else "vec0.so")
    p = MANAGED_BIN / "sqlite-vec" / name
    return p if p.exists() else None


def _probe_sqlite_vec(con=None, loadable=None, retries=3):
    """sqlite-vec 可用性探测：import → load → vec0 虚表冒烟。
    返回 (ok, info)。loadable: 兜底模式下的扩展文件路径。
    Windows 下 DLL 初始化存在间歇性失败，load 异常重试 3 次（与 _load_vec0 同策略）；
    sqlite_vec 包未安装属确定性缺失，不重试。"""
    import time as _time
    try:
        import sqlite_vec
    except ImportError as e:
        return False, {"error": f"sqlite_vec 未安装: {e}"}
    info = {"version": getattr(sqlite_vec, "__version__", "0.1.9")}
    last = None
    for attempt in range(retries):
        own = False
        try:
            c = con
            if c is None:
                c = sqlite3.connect(":memory:")
                own = True
            c.enable_load_extension(True)
            if loadable:
                c.load_extension(str(loadable))
            else:
                sqlite_vec.load(c)
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS vec_probe USING vec0("
                      "embedding float[1024])")
            c.execute("DROP TABLE IF EXISTS vec_probe")
            return True, info
        except Exception as e:
            last = e
            _time.sleep(0.05 * (attempt + 1))
        finally:
            if own:
                c.close()
    info["error"] = str(last)[:200]
    return False, info


def sqlite_vec_ready(force=False):
    """进程内缓存的 sqlite-vec 就绪状态（供闸门/检索路径快速判定）。"""
    global _SQLITE_VEC_STATE
    if force or _SQLITE_VEC_STATE is None:
        _SQLITE_VEC_STATE = _probe_sqlite_vec()
    return _SQLITE_VEC_STATE


def install_sqlite_vec():
    """安装 sqlite-vec 向量后端：PyPI 优先（当前解释器 = 专用运行时内即装入运行时 venv），
    失败时 GitHub loadable tarball 兜底（受管 bin/sqlite-vec/）。成功返回版本信息 dict；
    全部失败抛 RuntimeError（调用方据此回退 numpy）。"""
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", SQLITE_VEC_PACKAGE]
    index_url = os.environ.get("MYAGENTRAG_PIP_INDEX_URL")
    if index_url:
        cmd += ["--index-url", index_url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    ok, info = _probe_sqlite_vec()
    if ok:
        print(f"  ✅ sqlite-vec 就绪（{info.get('version', '')}）", file=sys.stderr)
        _record_dependency("sqlite-vec", info)
        return info
    print(f"  ⚠️ PyPI 安装后加载失败（{info.get('error', '')[:120]}），尝试 GitHub loadable 兜底...", file=sys.stderr)
    # GitHub loadable tarball 兜底
    machine = platform.machine().upper()
    if os.name == "nt":
        plat = "windows-x86_64"
    elif sys.platform == "darwin":
        plat = "macos-aarch64" if machine in ("ARM64", "AARCH64") else "macos-x86_64"
    else:
        plat = "linux-aarch64" if machine in ("ARM64", "AARCH64") else "linux-x86_64"
    asset = f"sqlite-vec-0.1.9-loadable-{plat}.tar.gz"
    url = f"https://github.com/asg017/sqlite-vec/releases/download/v0.1.9/{asset}"
    import tarfile
    target = MANAGED_BIN / "sqlite-vec"
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="myag_vec_dl_") as td:
        archive = _http_download(url, Path(td) / asset, "sqlite-vec 扩展")
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(td)
        ext_name = "vec0.dll" if os.name == "nt" else ("vec0.dylib" if sys.platform == "darwin" else "vec0.so")
        ext = next((q for q in Path(td).rglob(ext_name) if q.is_file()), None)
        if not ext:
            raise RuntimeError(f"下载包中未找到 {ext_name}")
        shutil.copy2(ext, target / ext_name)
    ok2, info2 = _probe_sqlite_vec(loadable=_sqlite_vec_loadable_path())
    if not ok2:
        raise RuntimeError(f"sqlite-vec 兜底安装后仍加载失败: {info2.get('error', '')}")
    print("  ✅ sqlite-vec 就绪（loadable 兜底）", file=sys.stderr)
    _record_dependency("sqlite-vec", info2)
    return info2


def _record_dependency(component, info):
    """依赖锁 manifest（方案 §8C.12）：记录组件版本与时间。"""
    import json as _json
    import datetime as _dt
    mf = MANAGED_HOME / "dependencies.json"
    try:
        data = {}
        if mf.exists():
            data = _json.loads(mf.read_text(encoding="utf-8"))
        data[component] = {"version": info.get("version", ""),
                           "updated_at": _dt.datetime.now().isoformat(timespec="seconds")}
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _find_model_file_embedding(model_id):
    spec = EMBEDDING_MODELS.get(model_id)
    if not spec:
        return None
    p = MANAGED_MODELS / "embedding" / model_id / spec["gguf_file"]
    return p if p.exists() and p.is_file() else None
