#!/usr/bin/env python3
"""
智能内容提取工具 v3.4 (smart-summarize)
功能：提取 YouTube/B站/网页/本地文件内容，不调用 LLM
支持格式：txt, md, pdf, docx, doc, epub, mp3, wav, mp4, mkv, 等

pi 迁移版（源自 OpenClaw smart-summarize v3.0），变更：
- v3.3: 音频转录只走 whisper.cpp，移除 faster-whisper 回退
- v3.4: 按操作系统寻找 whisper-cli，临时目录和 ffmpeg 查找跨平台化；cookies 改为显式环境变量
- v3.5: 运行时检测缺失组件（ffmpeg/whisper-cli/ggml 模型），提示大小并经用户确认后下载安装，随后继续原任务
- v3.2: 状态行改输出 stderr（不再污染重定向的 SRT/JSON 文件）
- 临时目录改用 tempfile（移除 ~/.openclaw 依赖）
- yt-dlp 参数修正：--js-runtime -> --js-runtimes（EJS 时代必需）
- 空 cookies 文件不再传给 yt-dlp
- Jina Reader URL 保留原 scheme（原版会把 https 目标降级为 http）
- PyMuPDF 新 API（pymupdf 优先，fitz 回退）
"""

import sys
import os
import json
import re
import argparse
import platform
import subprocess
import shutil
import tempfile
import importlib
from pathlib import Path
from datetime import datetime

# Windows 管道输出默认 GBK，emoji/特殊字符会 UnicodeEncodeError 崩溃，强制 UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 临时工作目录根：显式配置优先；Windows 保留本机约定，Unix/WSL 使用系统临时目录。
# 临时目录只保存本次运行的中间文件，不把用户机器路径写死到技能说明中。
def _default_temp_base_dir():
    configured = os.environ.get("SMART_SUMMARIZE_TMPDIR")
    if configured:
        return Path(configured).expanduser()
    return Path(tempfile.gettempdir())

TEMP_BASE_DIR = _default_temp_base_dir()

# 由用户确认后自动下载的组件（ffmpeg / whisper.cpp / ggml 模型）安装在此目录，
# 可用 SMART_SUMMARIZE_HOME 覆盖。
MANAGED_HOME = Path(os.environ.get(
    "SMART_SUMMARIZE_HOME",
    str(Path.home() / ".smart-summarize"),
)).expanduser()
MANAGED_BIN = MANAGED_HOME / "bin"
MANAGED_MODELS = MANAGED_HOME / "models"

def _sweep_stale_tmpdirs(max_age_hours=72):
    """清理异常退出遗留的 ss_* 临时目录（超过 max_age_hours 即删）"""
    try:
        now = datetime.now().timestamp()
        for p in TEMP_BASE_DIR.glob("ss_*"):
            if p.is_dir() and now - p.stat().st_mtime > max_age_hours * 3600:
                shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass

def make_tmpdir(prefix):
    """在受管理的临时根目录下创建本次运行的工作目录"""
    TEMP_BASE_DIR.mkdir(parents=True, exist_ok=True)
    _sweep_stale_tmpdirs()
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(TEMP_BASE_DIR)))

# ==================== 内容类型检测 ====================

def detect_content_type(url_or_path):
    if not url_or_path:
        return "unknown"
    url_lower = url_or_path.lower()
    if any(d in url_lower for d in ['youtube.com', 'youtu.be']):
        return "youtube"
    if any(d in url_lower for d in ['bilibili.com', 'b23.tv']):
        return "bilibili"
    if url_or_path.startswith(('http://', 'https://')):
        return "web"
    path = Path(url_or_path)
    if path.exists():
        ext = path.suffix.lower()
        if ext in ['.txt', '.md', '.markdown', '.rst', '.csv']:
            return "text"
        elif ext == '.pdf':
            return "pdf"
        elif ext in ['.docx', '.doc']:
            return "word"
        elif ext == '.epub':
            return "epub"
        elif ext in ['.mp3', '.wav', '.aac', '.m4a', '.flac', '.ogg', '.wma']:
            return "audio"
        elif ext in ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm']:
            return "video"
    return "unknown"


# ==================== YouTube 提取 ====================

def extract_video_id(url):
    match = re.search(r'(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})', url)
    return match.group(1) if match else None

def clean_subtitle(content):
    lines = content.split('\n')
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line or line.isdigit() or '-->' in line:
            continue
        line = re.sub(r'<[^>]+>', '', line)
        if line.upper() not in ['WEBVTT', 'NOTE'] and not re.match(r'(Kind|Language):', line):
            cleaned.append(line)
    return '\n'.join(cleaned)

def _youtube_cookies_path():
    """cookies 文件路径：环境变量优先，否则用用户受管目录下的约定位置。
    技能永不自动创建或收集 cookies；文件只由用户手动导出后放入。"""
    configured = os.environ.get("SMART_SUMMARIZE_YOUTUBE_COOKIES")
    if configured:
        return Path(configured).expanduser()
    return MANAGED_HOME / "cookies" / "youtube-cookies.txt"

def _yt_cookies_args():
    """cookies 文件存在且非空才启用（空文件会导致 yt-dlp 报错/行为异常）"""
    cookies = _youtube_cookies_path()
    if cookies.exists() and cookies.is_file() and cookies.stat().st_size > 0:
        return ['--cookies', str(cookies)]
    return []

def _find_ytdlp():
    """优先使用当前 Python 环境安装的 yt-dlp，再查 PATH。"""
    names = ("yt-dlp.exe", "yt-dlp") if os.name == "nt" else ("yt-dlp", "yt-dlp.exe")
    python_bin_dir = Path(sys.executable).resolve().parent
    for name in names:
        candidate = python_bin_dir / name
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return shutil.which("yt-dlp")

def extract_youtube(video_id):
    result = {"platform": "youtube", "video_id": video_id, "title": "", "author": "", "transcript": "", "success": False}
    try:
        tmpdir = make_tmpdir(f"ss_yt_{video_id}_")
        cookies_args = _yt_cookies_args()
        ytdlp = _find_ytdlp()
        if not ytdlp:
            result["error"] = "未找到 yt-dlp，请安装依赖并确保其位于当前 Python 环境或 PATH"
            return result

        info_cmd = [ytdlp, '--js-runtimes', 'node', '--dump-json', '--skip-download',
                    f'https://youtube.com/watch?v={video_id}'] + cookies_args
        r = subprocess.run(info_cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and r.stdout:
            try:
                info = json.loads(r.stdout.strip().split('\n')[0])
                result["title"] = info.get("title", "")
                result["author"] = info.get("uploader", "")
            except Exception:
                pass

        sub_cmd = [ytdlp, '--js-runtimes', 'node',
                   '--write-sub', '--write-auto-sub',
                   '--sub-lang', 'zh-CN,zh-TW,zh-Hans,zh-Hant,en',
                   '--skip-download', '--output', f'{tmpdir}/%(id)s',
                   f'https://youtube.com/watch?v={video_id}'] + cookies_args
        sub_r = subprocess.run(sub_cmd, capture_output=True, text=True, timeout=180)

        for f in sorted(tmpdir.glob(f"{video_id}.*")):
            if f.suffix in ('.srt', '.vtt'):
                result["transcript"] = clean_subtitle(f.read_text(encoding='utf-8', errors='ignore'))
                result["success"] = True
                break

        if not result["success"]:
            err_text = ((sub_r.stderr or '') + (sub_r.stdout or ''))[:4000]
            auth_signs = ("Sign in", "sign in", "not a bot", "age", "members-only",
                          "Private video", "login", "cookies")
            if any(s.lower() in err_text.lower() for s in auth_signs):
                cookies_path = _youtube_cookies_path()
                result["cookieHint"] = (
                    "YouTube 可能需要登录验证。请用浏览器扩展（如 Get cookies.txt LOCALLY）"
                    f"导出 youtube.com 的 Netscape 格式 cookies，保存到：{cookies_path}，然后重试。"
                    "（公开视频无需 cookies）"
                )
                print(f"  ⚠️ {result['cookieHint']}", file=sys.stderr)

        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:
        result["error"] = str(e)
    return result


# ==================== B站提取 ====================

def extract_bvid(url):
    match = re.search(r'(BV[0-9a-zA-Z]{10})', url)
    return match.group(1) if match else None

def extract_bilibili(bvid):
    result = {"platform": "bilibili", "bvid": bvid, "title": "", "author": "", "transcript": "", "success": False}
    try:
        import requests
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com'}

        r = requests.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", headers=headers, timeout=30)
        data = r.json()
        if data.get('code') == 0:
            result["title"] = data['data'].get('title', '')
            result["author"] = data['data'].get('owner', {}).get('name', '')
            cid = data['data'].get('cid', '')
            if cid:
                sr = requests.get(f"https://api.bilibili.com/x/player/wbi/v2?cid={cid}&bvid={bvid}", headers=headers, timeout=30)
                sd = sr.json()
                if sd.get('code') == 0:
                    subs = sd.get('data', {}).get('subtitle', {}).get('subtitles', [])
                    if subs:
                        sub_url = subs[0].get('subtitle_url', '')
                        if sub_url:
                            if sub_url.startswith('//'):
                                sub_url = 'https:' + sub_url
                            tr = requests.get(sub_url, headers=headers, timeout=30)
                            result["transcript"] = clean_subtitle('\n'.join([b.get('content', '') for b in tr.json().get('body', [])]))
                            result["success"] = True
    except Exception as e:
        result["error"] = str(e)
    return result


# ==================== 网页提取 ====================

def extract_web(url):
    result = {"platform": "web", "url": url, "title": "", "content": "", "success": False}
    try:
        import requests
        # 保留原 scheme：r.jina.ai/<原URL>（原版实现会把 https 降级为 http）
        jina_url = f"https://r.jina.ai/{url}"
        r = requests.get(jina_url, timeout=60)
        if r.status_code == 200:
            text = r.text
            lines = text.split('\n')
            if lines:
                result["title"] = lines[0].lstrip('Title: ').strip()
                result["content"] = '\n'.join(lines[1:]).strip()
                result["success"] = True
    except Exception as e:
        result["error"] = str(e)
    return result


# ==================== 本地文件提取 ====================

def extract_text_file(file_path):
    """提取纯文本文件"""
    encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1']
    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                return f.read()
        except Exception:
            continue
    return None

def extract_pdf_text(file_path):
    """提取 PDF 文件"""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return '\n'.join(text_parts) if text_parts else None
    except ImportError:
        pass
    except Exception as e:
        print(f"  ⚠️ pdfplumber 失败: {e}")

    try:
        try:
            import pymupdf as fitz_mod
        except ImportError:
            import fitz as fitz_mod  # 旧版回退
        text_parts = []
        with fitz_mod.open(file_path) as doc:
            for page in doc:
                text_parts.append(page.get_text())
        return '\n'.join(text_parts) if text_parts else None
    except ImportError:
        print("  ⚠️ 未安装 pdfplumber 或 PyMuPDF")
    except Exception as e:
        print(f"  ⚠️ PDF 提取错误: {e}")
    return None

def extract_word_text(file_path):
    """提取 Word 文档"""
    ext = Path(file_path).suffix.lower()
    if ext == '.docx':
        try:
            from docx import Document
            doc = Document(file_path)
            paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
            return '\n'.join(paragraphs) if paragraphs else None
        except ImportError:
            print("  ⚠️ 未安装 python-docx")
        except Exception as e:
            print(f"  ⚠️ Word 提取错误: {e}")
    elif ext == '.doc':
        try:
            result = subprocess.run(['pandoc', file_path, '-t', 'plain'], capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                return result.stdout
        except Exception:
            print("  ⚠️ pandoc 不可用")
    return None

def extract_epub_text(file_path):
    """提取 EPUB 电子书"""
    try:
        from ebooklib import epub
        book = epub.read_epub(file_path)
        text_parts = []
        for item in book.get_items():
            if item.get_type() == epub.ITEM_DOCUMENT:
                html = item.get_content().decode('utf-8', errors='ignore')
                text = re.sub(r'<[^>]+>', ' ', html)
                text = re.sub(r'\s+', ' ', text).strip()
                if text:
                    text_parts.append(text)
        return '\n'.join(text_parts) if text_parts else None
    except ImportError:
        print("  ⚠️ 未安装 ebooklib")
    except Exception as e:
        print(f"  ⚠️ EPUB 提取错误: {e}")
    return None

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
        "purpose": "Word (.docx) 文本提取",
    },
    "epub": {
        "packages": ["ebooklib"],
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
            import_name = "docx" if g == "docx" else g
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
    if kind.startswith("pip:"):
        group = kind.split(":", 1)[1]
        spec = PIP_LIB_GROUPS.get(group)
        pkgs = " ".join(spec["packages"]) if spec else group
        purpose = spec["purpose"] if spec else "Python 库"
        return {"kind": kind, "name": pkgs,
                "purpose": purpose,
                "source": f"安装到当前 Python 解释器：\"{sys.executable}\" -m pip install {pkgs}",
                "est_size": "合计数 MB"}
    if kind == "ffmpeg":
        url = _ffmpeg_source_url()
        size = _content_length(url) if url else 0
        return {"kind": kind, "name": "ffmpeg",
                "purpose": "音视频解码与音频提取",
                "source": url or "系统包管理器（apt/dnf/pacman/brew）",
                "est_size": _human_size(size) if size else "约 100 MB（以实际下载为准）"}
    if kind == "whisper-cli":
        return {"kind": kind, "name": "whisper-cli (whisper.cpp)",
                "purpose": "本地语音转录",
                "source": "依次尝试：brew 预编译包 → GitHub 官方预编译版 → 源码构建（需 git/cmake/编译器）",
                "est_size": "仓库约 60MB + 编译时间"}
    model_name = kind.split(":", 1)[1]
    fname = WHISPERCPP_GGML_MAP.get(model_name, f"ggml-{model_name}.bin")
    size = _content_length(MODEL_URL_BASE + fname) or KNOWN_MODEL_SIZES.get(fname, 0)
    return {"kind": kind, "name": fname,
            "purpose": f"whisper 转录模型 ({model_name})",
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
        return install_ffmpeg()
    if kind == "whisper-cli":
        return install_whispercli()
    if kind.startswith("model:"):
        return install_model(kind.split(":", 1)[1])
    raise RuntimeError(f"未知组件类型: {kind}")

# ==================== whisper.cpp（唯一转录引擎） ====================
# 是否使用 GPU 取决于用户安装的 whisper.cpp 构建：Vulkan/Metal/CUDA 构建会使用
# 对应后端；普通 CPU 构建不会。脚本不强行下载或编译二进制。
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

def _whispercpp_transcribe(file_path, model_name, want_srt):
    """使用本机 whisper.cpp 构建转录；成功返回 SRT 或纯文本，失败返回 None。"""
    cli = _find_whispercpp_cli()
    ggml = _find_model_file(model_name)
    ffmpeg = _find_ffmpeg()
    if not (cli and ggml and ffmpeg):
        return None
    tmpdir = make_tmpdir(f"ss_wcpp_{Path(file_path).stem}_")
    try:
        wav = tmpdir / "audio16k.wav"
        cmd = [ffmpeg, "-i", str(file_path), "-vn", "-acodec", "pcm_s16le",
               "-ar", "16000", "-ac", "1", str(wav), "-y"]
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=600)
        if r.returncode != 0 or not wav.exists():
            return None
        out_base = tmpdir / "out"
        # 不用 -np：它会把 ggml 后端日志一起吞掉，导致 GPU 后端上报失效；
        # 日志走 stderr，不影响 stdout 的 SRT/JSON 输出
        cmd = [str(cli), "-m", str(ggml), "-f", str(wav), "-l", "auto",
               "-osrt", "-of", str(out_base)]
        if NO_GPU:
            cmd += ["-ng"]
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=3600)
        srt_file = tmpdir / "out.srt"
        if not srt_file.exists():
            return None
        content = srt_file.read_text(encoding="utf-8", errors="replace")
        backend = _detect_backend_from_log(r.stderr or "")
        if NO_GPU:
            print(f"  🖥 转录完成（已强制 CPU）: {model_name}", file=sys.stderr)
        elif backend:
            print(f"  🎮 转录完成（GPU 加速: {backend}）: {model_name}", file=sys.stderr)
        else:
            print(f"  ✅ 转录完成（CPU）: {model_name}", file=sys.stderr)
        if want_srt:
            return content.strip() or None
        lines = [l.strip() for l in content.splitlines()
                 if l.strip() and "-->" not in l and not re.fullmatch(r"\d+", l.strip())]
        text = " ".join(lines).strip()
        return text if len(text) > 10 else None
    except Exception as e:
        print(f"  ⚠️ whisper.cpp 转录错误: {e}", file=sys.stderr)
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

NO_GPU = False

def _detect_backend_from_log(stderr_text):
    """从 whisper-cli 的 stderr 判断实际使用的计算后端，返回可读描述。"""
    text = stderr_text or ""
    # ggml_vulkan 设备行两种格式：新版 "0 | AMD Radeon..."，旧版 "0 = AMD Radeon..."
    m = re.search(r"ggml_vulkan:.*?\d+\s*[\|=]\s*([A-Za-z][^\r\n]+)", text)
    if m:
        return f"Vulkan: {m.group(1).strip()[:80]}"
    m = re.search(r"ggml_cuda[^\n]*?device\s*\d*\s*\|?\s*([^\r\n]*)", text, re.I)
    if m:
        return f"CUDA: {m.group(1).strip()[:80]}"
    if "ggml_metal" in text or "Metal" in text:
        return "Metal"
    if re.search(r"ggml_hip|ROCm", text, re.I):
        return "ROCm/HIP"
    return None

def extract_audio_text(file_path, model="large-v3-turbo"):
    """提取音频文件内容（语音转文字）- 返回纯文本"""
    kinds = _missing_dep_kinds(model)
    if kinds:
        raise MissingDependencyError(kinds)
    return _whispercpp_transcribe(file_path, model, want_srt=False)


def extract_audio_srt(file_path, model="large-v3-turbo"):
    """提取音频文件内容（语音转文字）- 返回 SRT 格式"""
    kinds = _missing_dep_kinds(model)
    if kinds:
        raise MissingDependencyError(kinds)
    return _whispercpp_transcribe(file_path, model, want_srt=True)

def extract_video_text(file_path):
    """提取视频文件内容"""
    tmpdir = make_tmpdir(f"ss_vid_{Path(file_path).stem}_")

    try:
        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            raise MissingDependencyError(["ffmpeg"])
        # 先尝试提取内置字幕
        try:
            output_file = tmpdir / 'subtitle.srt'
            cmd = [ffmpeg, '-i', file_path, '-map', '0:s:0', str(output_file), '-y']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and output_file.exists():
                content = output_file.read_text(encoding='utf-8', errors='ignore')
                text = re.sub(r'\d+\n\d{2}:\d{2}:\d{2}.*?\n\n', '', content, flags=re.DOTALL)
                if text.strip():
                    return text.strip()
        except Exception:
            pass

        # 提取音频并转录
        audio_file = tmpdir / 'audio.wav'
        cmd = [ffmpeg, '-i', file_path, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', str(audio_file), '-y']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and audio_file.exists():
            return extract_audio_text(str(audio_file))
    except MissingDependencyError:
        raise
    except Exception as e:
        print(f"  ⚠️ 视频处理错误: {e}", file=sys.stderr)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return None

def extract_local_file(file_path, output_format='json', model='large-v3-turbo'):
    """根据文件类型自动选择提取方法"""
    path = Path(file_path)
    if not path.exists():
        return {"error": f"文件不存在: {file_path}", "success": False}

    ext = path.suffix.lower()
    result = {"platform": "file", "filepath": str(path), "filename": path.name, "content": "", "type": ext[1:], "success": False}

    content = None
    if ext in ['.txt', '.md', '.markdown', '.rst', '.csv']:
        content = extract_text_file(file_path)
    elif ext == '.pdf':
        content = extract_pdf_text(file_path)
    elif ext in ['.docx', '.doc']:
        content = extract_word_text(file_path)
    elif ext == '.epub':
        content = extract_epub_text(file_path)
    elif ext in ['.mp3', '.wav', '.aac', '.m4a', '.flac', '.ogg', '.wma']:
        # 支持 SRT 格式输出
        if output_format == 'srt':
            content = extract_audio_srt(file_path, model=model)
        else:
            content = extract_audio_text(file_path, model=model)
    elif ext in ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm']:
        content = extract_video_text(file_path)

    if content:
        result["content"] = content
        result["success"] = True
    else:
        result["error"] = f"无法提取 {ext} 文件内容"

    return result


# ==================== 主函数 ====================

def _run_extraction(args):
    target = args.url or args.file
    content_type = detect_content_type(target)

    # Python 库依赖预检（与 ffmpeg/whisper 组件同一确认机制）：
    # 只在实际用到该格式时检测，缺失则列出清单，确认后用当前解释器 pip 安装。
    lib_groups = []
    if content_type == "youtube":
        lib_groups.append("yt-dlp")
    elif content_type in ("bilibili", "web"):
        lib_groups.append("requests")
    elif content_type in ("audio", "video"):
        # 下载 ffmpeg/whisper 组件的确认下载链路本身依赖 requests，
        # 全新环境下先确保它可用，否则用户确认后安装会崩
        lib_groups.append("requests")
    elif content_type == "pdf":
        lib_groups.append("pdf")
    elif content_type == "epub":
        lib_groups.append("epub")
    elif content_type == "word" and Path(args.file).suffix.lower() == ".docx":
        lib_groups.append("docx")
    if lib_groups:
        kinds = _missing_pipelib_kinds(lib_groups)
        if kinds:
            raise MissingDependencyError(kinds)

    if content_type == "youtube":
        video_id = extract_video_id(args.url)
        return extract_youtube(video_id) if video_id else {"error": "无法提取视频ID", "success": False}
    if content_type == "bilibili":
        bvid = extract_bvid(args.url)
        return extract_bilibili(bvid) if bvid else {"error": "无法提取BV号", "success": False}
    if content_type == "web":
        return extract_web(args.url)
    if content_type in ["text", "pdf", "word", "epub", "audio", "video"]:
        return extract_local_file(args.file, output_format=args.output, model=args.model)
    return {"error": f"不支持的内容类型: {content_type}", "success": False}

def _handle_missing_deps(args, err):
    """列出缺失组件（名称/用途/来源/预计大小），经用户确认后下载安装并继续原任务。"""
    items = [_dep_detail(k) for k in err.kinds]
    print("\n⚠️ 提取/转录所需的以下组件缺失：", file=sys.stderr)
    for it in items:
        print(f"  • {it['name']}（{it['purpose']}）"
              f"\n    来源: {it['source']}\n    预计大小: {it['est_size']}", file=sys.stderr)

    allowed = args.download_deps
    if not allowed:
        interactive = False
        try:
            interactive = bool(sys.stdin and sys.stdin.isatty())
        except Exception:
            interactive = False
        if not interactive:
            print("\n（非交互环境：可在用户确认后加 --download-deps 重新运行）", file=sys.stderr)
        else:
            try:
                ans = input("\n是否立即下载并安装以上组件，然后继续任务? [y/N] ")
                allowed = ans.strip().lower() in ("y", "yes")
            except (EOFError, KeyboardInterrupt, OSError):
                allowed = False

    if not allowed:
        return {"success": False,
                "error": "缺少必需组件，未下载。请确认后重试。",
                "missing": items}

    all_ok = True
    for it in items:
        try:
            path = _install_dep(it["kind"])
            print(f"  ✅ 已安装 {it['name']} → {path}", file=sys.stderr)
        except Exception as e:
            all_ok = False
            print(f"  ❌ {it['name']} 安装失败: {e}", file=sys.stderr)
    if not all_ok:
        return {"success": False,
                "error": "部分组件安装失败（见 stderr）。可手动安装后重试。",
                "missing": items}
    print("  ↻ 组件就绪，继续执行原任务...", file=sys.stderr)
    return _run_extraction(args)

def main():
    parser = argparse.ArgumentParser(description='智能内容提取工具')
    parser.add_argument('--url', help='要提取的 URL')
    parser.add_argument('--file', help='要提取的本地文件')
    parser.add_argument('--output', choices=['json', 'text', 'srt'], default='json', help='输出格式 (srt 仅支持音频/视频转字幕)')
    parser.add_argument('--model', default='large-v3-turbo', help='Whisper 模型名称 (默认: large-v3-turbo; 可选 large-v3-turbo-q5_0 快速档)')
    parser.add_argument('--no-gpu', action='store_true', help='强制 CPU 转录（禁用 GPU 后端）')
    parser.add_argument('--download-deps', action='store_true',
                        help='缺组件时跳过交互确认，直接下载安装（用于 agent 代为确认后调用）')
    args = parser.parse_args()
    global NO_GPU
    NO_GPU = args.no_gpu

    if not args.url and not args.file:
        print("错误：请提供 --url 或 --file", file=sys.stderr)
        sys.exit(1)

    # 本地文件不存在时提前报错（否则会被当成 unknown 类型，报错误导人）
    if args.file and not Path(args.file).exists():
        print(json.dumps({"error": f"文件不存在: {args.file}", "success": False}, ensure_ascii=False))
        sys.exit(1)

    try:
        result = _run_extraction(args)
    except MissingDependencyError as e:
        result = _handle_missing_deps(args, e)

    if args.output == 'json':
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.output == 'srt':
        # SRT 直接输出内容
        if result.get("success"):
            print(result.get('content', ''))
        else:
            print(f"提取失败: {result.get('error', '未知错误')}", file=sys.stderr)
            sys.exit(1)
    else:
        # text 格式
        if result.get("success"):
            print(f"标题: {result.get('title', result.get('filename', ''))}")
            if result.get('author'):
                print(f"作者: {result['author']}")
            print(f"\n内容:\n{result.get('transcript', result.get('content', ''))}")
        else:
            print(f"提取失败: {result.get('error', '未知错误')}", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
