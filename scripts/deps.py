# -*- coding: utf-8 -*-
"""运行时依赖体系 — smart-summarize v0.6.0 模块化拆分

组件依赖检测与确认安装（ffmpeg/whisper-cli/ggml 模型/pip 库）与各组件定位：
缺失时列出清单（名称/用途/来源/预计大小），经用户确认后下载安装并继续原任务。

依赖方向：slicing ← deps ← transcribe ← extract（extractors 独立）。
"""
import importlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 受管组件根目录（与主入口共享，可用 SMART_SUMMARIZE_HOME 覆盖）
MANAGED_HOME = Path(os.environ.get(
    "SMART_SUMMARIZE_HOME",
    str(Path.home() / ".smart-summarize"),
)).expanduser()
MANAGED_BIN = MANAGED_HOME / "bin"
MANAGED_MODELS = MANAGED_HOME / "models"


def _default_whispercpp_dir():
    return MANAGED_BIN


def _default_whispercpp_models_dir():
    return MANAGED_MODELS


WHISPERCPP_DIR = Path(os.environ.get(
    "SMART_SUMMARIZE_WHISPERCPP_DIR",
    str(_default_whispercpp_dir()),
)).expanduser()
WHISPERCPP_MODELS_DIR = Path(os.environ.get(
    "SMART_SUMMARIZE_WHISPERCPP_MODELS_DIR",
    str(_default_whispercpp_models_dir()),
)).expanduser()

# faster-whisper 风格模型名 -> whisper.cpp ggml 模型文件
WHISPERCPP_GGML_MAP = {
    "large-v3-turbo": "ggml-large-v3-turbo.bin",          # fp16，参考精度（默认）
    "large-v3-turbo-q5_0": "ggml-large-v3-turbo-q5_0.bin",  # 量化快速档
}

# ==================== 组件定位（环境变量 → PATH → 受管目录） ====================

def _find_ffmpeg():
    configured = os.environ.get("SMART_SUMMARIZE_FFMPEG")
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
    configured = os.environ.get("SMART_SUMMARIZE_WHISPERCPP_CLI")
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
    """所需组件缺失；kinds: 'ffmpeg' / 'whisper-cli' / 'model:<name>' / 'pip:<group>'"""
    def __init__(self, kinds):
        self.kinds = kinds
        super().__init__("缺少组件: " + ", ".join(kinds))

# ==================== Python 库依赖（首次使用时运行时检测、确认后安装） ====================
# 设计原则与 ffmpeg/whisper 组件一致：初始安装不预装、不自动下载；
# 实际用到该格式时才检测，列出清单经用户确认后用当前解释器 pip 安装。
PIP_LIB_GROUPS = {
    "pdf": {
        "packages": ["pdfplumber", "pymupdf"],
        "purpose": "PDF 文本提取（pdfplumber 或 PyMuPDF 任一即可）",
    },
    "docx": {
        "packages": ["python-docx"],
        "import": "docx",
        "purpose": "Word (.docx) 文本提取",
    },
    "excel": {
        "packages": ["openpyxl"],
        "import": "openpyxl",
        "purpose": "Excel (.xlsx/.xlsm) 表格文本提取",
    },
    "pptx": {
        "packages": ["python-pptx"],
        "import": "pptx",
        "purpose": "PowerPoint (.pptx) 幻灯片文本提取",
    },
    "epub": {
        "packages": ["ebooklib"],
        "import": "ebooklib",
        "purpose": "EPUB 电子书文本提取",
    },
    "yt-dlp": {
        "packages": ["yt-dlp"],
        "purpose": "YouTube 字幕与元数据提取",
    },
    "requests": {
        "packages": ["requests"],
        "purpose": "网页正文与 B 站字幕提取",
    },
}

def _import_ok(name):
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False

def _missing_pipelib_kinds(groups):
    """按用途组检测缺失的 Python 库，返回 'pip:<group>' kind 列表。"""
    kinds = []
    for g in groups:
        if g == "pdf":
            if not (_import_ok("pdfplumber") or _import_ok("pymupdf") or _import_ok("fitz")):
                kinds.append(f"pip:{g}")
        elif g == "yt-dlp":
            if not _import_ok("yt_dlp") and not shutil.which("yt-dlp"):
                kinds.append(f"pip:{g}")
        else:
            spec = PIP_LIB_GROUPS.get(g)
            import_name = spec.get("import", g) if spec else ("docx" if g == "docx" else g)
            if spec and not _import_ok(import_name):
                kinds.append(f"pip:{g}")
    return kinds

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
    with tempfile.TemporaryDirectory(prefix="ss_ffmpeg_dl_") as td:
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
            with tempfile.TemporaryDirectory(prefix="ss_wcpp_dl_") as td:
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
                              "或用 SMART_SUMMARIZE_WHISPERCPP_CLI 指向已有的 GPU 构建", file=sys.stderr)
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
    custom_flags = os.environ.get("SMART_SUMMARIZE_WHISPERCPP_CMAKE_FLAGS", "").split()
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
    configured = os.environ.get("SMART_SUMMARIZE_WHISPERCPP_MODELS_DIR")
    if configured:
        return Path(configured).expanduser()
    return MANAGED_MODELS

def install_model(model_name):
    """用户确认后从 HuggingFace 下载 ggml 模型，返回模型文件路径"""
    fname = WHISPERCPP_GGML_MAP.get(model_name, f"ggml-{model_name}.bin")
    dest = _model_download_dir() / fname
    if dest.exists():
        return dest
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
    if kind.startswith("pip:"):
        group = kind.split(":", 1)[1]
        spec = PIP_LIB_GROUPS.get(group)
        pkgs = " ".join(spec["packages"]) if spec else group
        purpose_key = f"dep_purpose_{group}"
        if purpose_key in messages.MESSAGES:
            purpose = messages.msg(purpose_key)
        else:
            purpose = spec["purpose"] if spec else messages.msg("dep_unknown_group")
        return {"kind": kind, "name": pkgs,
                "purpose": purpose,
                "source": messages.msg("dep_source_pip", python=sys.executable, pkgs=pkgs),
                "est_size": messages.msg("dep_size_pip")}
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
    if kind.startswith("pip:"):
        group = kind.split(":", 1)[1]
        spec = PIP_LIB_GROUPS[group]
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + spec["packages"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            manual = " ".join(cmd)
            tail = ((r.stderr or "") + (r.stdout or "")).strip()[-300:]
            raise RuntimeError(f"pip 安装失败: {tail}；可手动执行以下命令 {manual}")
        return f"{Path(sys.executable)} -m pip（{' '.join(spec['packages'])}）"
    if kind == "whisper-cli":
        return install_whispercli()
    if kind.startswith("model:"):
        return install_model(kind.split(":", 1)[1])
    raise RuntimeError(f"未知组件类型: {kind}")
