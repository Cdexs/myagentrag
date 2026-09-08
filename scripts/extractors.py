# -*- coding: utf-8 -*-
"""内容提取器模块 — smart-summarize v0.6.0 模块化拆分

覆盖：类型检测 / YouTube / B站 / 网页 / 本地文件（文本、PDF、Word、EPUB、Excel、PowerPoint）
原则：只提取，不调用 LLM；cookies 永不自动创建或收集。
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from slicing import make_tmpdir
import messages

# 受管组件根目录（与主入口共享，可用 SMART_SUMMARIZE_HOME 覆盖）
MANAGED_HOME = Path(os.environ.get(
    "SMART_SUMMARIZE_HOME",
    str(Path.home() / ".smart-summarize"),
)).expanduser()


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
        elif ext in ['.xlsx', '.xlsm']:
            return "excel"
        elif ext == '.pptx':
            return "pptx"
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


def _bilibili_cookie_header():
    """B站 cookies：与 YouTube 同样的隐私规则，只走显式配置（环境变量或受管目录）。
    支持 Netscape 格式（yt-dlp/浏览器导出）与原生 Cookie 头格式两种文件。
    返回 Cookie 头值或空字符串。"""
    configured = os.environ.get("SMART_SUMMARIZE_BILIBILI_COOKIES")
    cookies = Path(configured).expanduser() if configured else MANAGED_HOME / "cookies" / "bilibili-cookies.txt"
    if not (cookies.exists() and cookies.is_file() and cookies.stat().st_size > 0):
        return ""
    try:
        text = cookies.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if "# Netscape" in text[:200]:
        # Netscape 格式：domain\tflag\tpath\tsecure\texpiry\tname\tvalue（跳过注释行，取 .bilibili.com 域）
        pairs = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) == 7 and "bilibili" in parts[0]:
                pairs.append(f"{parts[5]}={parts[6]}")
        return "; ".join(pairs)
    # 非 Netscape：按 Cookie 头原样使用（如 "SESSDATA=xxx; bili_jct=yyy"）
    stripped = text.strip()
    if "=" in stripped:
        return stripped
    messages.warn("bilibili_cookie_bad", path=cookies)
    return ""


def _find_ytdlp():
    """优先专用运行时 venv（库随运行时预装），其次当前 Python 环境与 PATH。"""
    names = ("yt-dlp.exe", "yt-dlp") if os.name == "nt" else ("yt-dlp", "yt-dlp.exe")
    bases = []
    try:
        import runtime
        venv_bin = runtime.venv_bin_dir()
        if venv_bin:
            bases.append(venv_bin)
    except Exception:
        pass
    bases.append(Path(sys.executable).resolve().parent)
    for base in bases:
        for name in names:
            candidate = base / name
            if candidate.exists() and candidate.is_file():
                return str(candidate)
    return shutil.which("yt-dlp")


def extract_youtube(video_id):
    result = {"platform": "youtube", "video_id": video_id, "title": "", "author": "", "transcript": "", "success": False}
    try:
        import requests
        cookies_args = _yt_cookies_args()
        ytdlp = _find_ytdlp()
        if not ytdlp:
            result["error"], result["error_i18n"] = messages.err_field("ytdlp_missing")
            return result

        # 先取元数据（标题/作者），失败不阻塞字幕路径
        try:
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
        except Exception:
            pass

        tmpdir = make_tmpdir(f"ss_yt_{video_id}_")
        try:
            sub_cmd = [ytdlp, '--js-runtimes', 'node',
                       '--write-sub', '--write-auto-sub',
                       '--sub-lang', 'zh-CN,zh-TW,zh-Hans,zh-Hant,en',
                       '--skip-download', '--output', str(tmpdir / '%(id)s'),
                       f'https://youtube.com/watch?v={video_id}'] + cookies_args
            subprocess.run(sub_cmd, capture_output=True, timeout=180)

            for f in sorted(tmpdir.glob(f"{video_id}.*")):
                if f.suffix in ('.srt', '.vtt'):
                    # 原始字幕一并返回（入库 workspace 时存为 source/ 快照并解析时间戳）
                    result["raw_subtitle"] = f.read_text(encoding='utf-8', errors='ignore')
                    result["raw_subtitle_ext"] = f.suffix[1:]
                    result["transcript"] = clean_subtitle(result["raw_subtitle"])
                    result["success"] = True
                    break
        finally:
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
        cookie_header = _bilibili_cookie_header()
        if cookie_header:
            headers['Cookie'] = cookie_header
            messages.warn("bilibili_cookie_ok")
        # B站是国内站：Windows 系统代理（注册表）常会把国内站转发失败（SSL EOF），
        # 默认绕过系统代理直连；用户显式设置 SMART_SUMMARIZE_PROXY 时尊重该代理。
        if os.environ.get("SMART_SUMMARIZE_PROXY"):
            proxies = None  # 交给 requests/环境变量处理
        elif os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"):
            proxies = None  # 用户显式设置了终端代理，尊重之
        else:
            proxies = {"http": None, "https": None}

        r = requests.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", headers=headers, timeout=30, proxies=proxies)
        data = r.json()
        if data.get('code') == 0:
            result["title"] = data['data'].get('title', '')
            result["author"] = data['data'].get('owner', {}).get('name', '')
            cid = data['data'].get('cid', '')
            if cid:
                sr = requests.get(f"https://api.bilibili.com/x/player/wbi/v2?cid={cid}&bvid={bvid}", headers=headers, timeout=30, proxies=proxies)
                sd = sr.json()
                if sd.get('code') == 0:
                    subs = sd.get('data', {}).get('subtitle', {}).get('subtitles', [])
                    if subs:
                        sub_url = subs[0].get('subtitle_url', '')
                        if sub_url:
                            if sub_url.startswith('//'):
                                sub_url = 'https:' + sub_url
                            tr = requests.get(sub_url, headers=headers, timeout=30, proxies=proxies)
                            body = tr.json().get('body', [])
                            # B站字幕 JSON 自带 from/to 秒级时间戳 → 段数组（入库时做时间戳索引）
                            result["subtitle_segments"] = [
                                {"start_ms": int(round(b.get('from', 0) * 1000)),
                                 "end_ms": int(round(b.get('to', 0) * 1000)),
                                 "text": b.get('content', '')}
                                for b in body if b.get('content')]
                            result["raw_subtitle"] = json.dumps(body, ensure_ascii=False)
                            result["raw_subtitle_ext"] = "json"
                            result["transcript"] = clean_subtitle('\n'.join([b.get('content', '') for b in body]))
                            result["success"] = True
    except Exception as e:
        result["error"] = str(e)
    return result


# ==================== 网页提取 ====================

def extract_web(url):
    result = {"platform": "web", "url": url, "title": "", "content": "", "success": False}
    try:
        import requests
        # 保留原 scheme：r.jina.ai/<原URL>
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


class Extraction:
    """提取结果：text（将一字不改写入 full.md）+ srcmap（源位置账本，可 None）。

    offset 不变式：srcmap 的偏移以 text 为唯一坐标系——text 到 full.md 之间
    不允许任何改写（ws_ingest content 路径已保证）；未来引入归一化必须同步变换 srcmap。
    """
    __slots__ = ("text", "srcmap")

    def __init__(self, text, srcmap=None):
        self.text = text
        self.srcmap = srcmap


def _docx_heading_level(para):
    """python-docx 标题级别：style_id "Heading1".."Heading6"（style_id 不随界面语言本地化）"""
    try:
        sid = para.style.style_id or ""
    except Exception:
        return None
    if sid.startswith("Heading"):
        try:
            return max(1, min(6, int(sid[7:])))
        except ValueError:
            return None
    return None


_PDF_NOISE_RES = [
    # 打印页脚签名（Effective STL 类书页）：保守匹配，正文行不受影响
    re.compile(r"file:///\S*\(\d+ of \d+\)"),        # file:///D|/...item_14.html (1 of 3)2005-4-26 ...
    re.compile(r"\(\d+ of \d+\)\s*\d{4}-\d+-\d+\s+\d{1,2}:\d{2}:\d{2}"),
    re.compile(r"^\s*\d{4}-\d+-\d+\s+\d{1,2}:\d{2}:\d{2}\s*$"),
]


def _pdf_noise_filter(page_text):
    """逐行过滤 PDF 页脚噪声；在页账本记账前调用，offset 不变式保持。

    加宽规则（S9）：行首 file:/// 且行内无 CJK、长度 <100 → 判页脚
    （正文引用 file:/// 通常是含中文说明的句子行，不受影响）
    """
    out = []
    for ln in page_text.split("\n"):
        st = ln.strip()
        if any(r.search(st) for r in _PDF_NOISE_RES):
            continue
        if (st.startswith("file:///") and len(st) < 100
                and not any("\u4e00" <= c <= "\u9fff" for c in st)):
            continue
        out.append(ln)
    return "\n".join(out)


def extract_pdf_text(file_path):
    """提取 PDF 文本 + 源位置账本 pages=[[起始偏移, 页码], ...]（§8D 页偏移记账）"""
    try:
        import pdfplumber
        text_parts, pages = [], []
        off = 0
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                page_text = _pdf_noise_filter(page.extract_text() or "")
                pages.append([off, i])
                text_parts.append(page_text)
                off += len(page_text) + 1          # +1 = 页间 '\n' 分隔符
        if not text_parts:
            return None
        return Extraction("\n".join(text_parts), {"kind": "pdf", "pages": pages})
    except ImportError:
        pass
    except Exception as e:
        messages.warn("warn_pdfplumber_fail", err=e)

    try:
        try:
            import pymupdf as fitz_mod
        except ImportError:
            import fitz as fitz_mod  # 旧版回退
        text_parts, pages = [], []
        off = 0
        with fitz_mod.open(file_path) as doc:
            try:
                outline = doc.get_toc()  # [[level, title, 页码(1-based)], ...]
            except Exception:
                outline = []
            for i, page in enumerate(doc, 1):
                t = _pdf_noise_filter(page.get_text())
                pages.append([off, i])
                text_parts.append(t)
                off += len(t) + 1                  # +1 = 页间 '\n' 分隔符
        if not text_parts:
            return None
        srcmap = {"kind": "pdf", "pages": pages}
        if outline:
            srcmap["outline"] = outline
        return Extraction("\n".join(text_parts), srcmap)
    except ImportError:
        messages.warn("warn_no_pdf_lib")
    except Exception as e:
        messages.warn("warn_doc_error", doc="PDF", err=e)
    return None


def extract_word_text(file_path):
    """提取 Word 文档；docx 标题样式归一化为 markdown # 标记（§8D 结构上岸）"""
    ext = Path(file_path).suffix.lower()
    if ext == '.docx':
        try:
            from docx import Document
            doc = Document(file_path)
            parts = []
            for para in doc.paragraphs:
                if not para.text.strip():
                    continue
                lv = _docx_heading_level(para)
                parts.append(("#" * lv + " " + para.text.strip()) if lv else para.text)
            return Extraction("\n".join(parts)) if parts else None
        except ImportError:
            messages.warn("warn_no_lib", lib="python-docx")
        except Exception as e:
            messages.warn("warn_doc_error", doc="Word", err=e)
    elif ext == '.doc':
        try:
            result = subprocess.run(['pandoc', file_path, '-t', 'plain'], capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                return result.stdout
        except Exception:
            messages.warn("warn_pandoc_missing")
    return None


_H_TAG_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", re.I | re.S)


def _html_to_structured_text(html):
    """HTML → 文本；h1-h6 归一化为 markdown # 标记（§8D），保留换行结构"""
    html = _H_TAG_RE.sub(
        lambda m: "\n\n" + "#" * int(m.group(1)) + " "
        + re.sub(r"<[^>]+>", "", m.group(2)).strip() + "\n\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[^\S\n]+", " ", text)   # 折叠空白但保留换行
    lines = [ln.strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def extract_epub_text(file_path):
    """提取 EPUB 电子书（spine 阅读序优先）+ 章节账本 chapters=[[偏移, 序号, 标题], ...]"""
    try:
        import ebooklib
        from ebooklib import epub
        book = epub.read_epub(file_path)
        # spine = 阅读顺序；nav/ncx 为结构性导航页，不入正文；不可用时回退 manifest 序
        try:
            items = [it for it in (book.get_item_with_id(sid) for sid, *_r in (book.spine or []))
                     if it is not None and it.get_type() == ebooklib.ITEM_DOCUMENT
                     and it.id not in ("nav", "ncx")]
        except Exception:
            items = []
        if not items:
            # ITEM_DOCUMENT 等常量在顶层 ebooklib 模块（ebooklib 0.20 起不再暴露
            # 到 ebooklib.epub 命名空间，官方文档亦用 ebooklib.ITEM_DOCUMENT）
            items = [it for it in book.get_items()
                     if it.get_type() == ebooklib.ITEM_DOCUMENT
                     and it.id not in ("nav", "ncx")]
        toc_titles = {}

        def _walk_toc(toc):
            for it in toc:
                if isinstance(it, tuple):
                    _walk_toc(it[1])
                elif getattr(it, "href", None) and getattr(it, "title", None):
                    toc_titles.setdefault(it.href.split("#")[0], it.title)

        try:
            _walk_toc(book.toc)
        except Exception:
            toc_titles = {}
        parts, chapters, off, n = [], [], 0, 0
        for item in items:
            html = item.get_content().decode('utf-8', errors='ignore')
            text = _html_to_structured_text(html)
            if not text:
                continue
            n += 1
            chapters.append([off, n, toc_titles.get(item.get_name())])
            parts.append(text)
            off += len(text) + 1
        if not parts:
            return None
        srcmap = {"kind": "epub", "chapters": chapters} if chapters else None
        return Extraction("\n".join(parts), srcmap)
    except ImportError:
        messages.warn("warn_no_lib", lib="ebooklib")
    except Exception as e:
        messages.warn("warn_doc_error", doc="EPUB", err=e)
    return None


def extract_excel_text(file_path):
    """提取 Excel (.xlsx/.xlsm)：每个工作表一段，行以 " | " 连接"""
    try:
        from openpyxl import load_workbook
    except ImportError:
        messages.warn("warn_no_lib", lib="openpyxl")
        return None
    try:
        wb = load_workbook(file_path, read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            parts.append(f"## 工作表: {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c).strip()[:500] for c in row]
                if any(cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts) if parts else None
    except Exception as e:
        messages.warn("warn_doc_error", doc="Excel", err=e)
        return None


def extract_pptx_text(file_path):
    """提取 PowerPoint (.pptx)：还原幻灯片内结构层级——
    标题占位符 → "## 幻灯片 N: 标题"；副标题/节标题 → "### ..."；
    正文与表格 → 普通行；组合形状递归展开；含演讲者备注"""
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        from pptx.enum.shapes import PP_PLACEHOLDER
    except ImportError:
        messages.warn("warn_no_lib", lib="python-pptx")
        return None

    def _shape_kind(shape):
        """返回 (kind, ph_type)：kind ∈ title/subtitle/body/table/group"""
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            return ("group", None)
        ph = getattr(shape, "placeholder_format", None)
        ph_type = None
        try:
            ph_type = ph.type if ph is not None and ph.idx is not None else None
        except Exception:
            ph_type = None
        if ph_type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
            return ("title", ph_type)
        if ph_type == PP_PLACEHOLDER.SUBTITLE:
            return ("subtitle", ph_type)
        if getattr(shape, "has_table", False):
            return ("table", ph_type)
        return ("body", ph_type)

    def _collect(shape, lines, title_seen):
        kind, _ = _shape_kind(shape)
        if kind == "group":
            for sub in shape.shapes:
                _collect(sub, lines, title_seen)
            return
        if kind == "table":
            for row in shape.table.rows:
                cells = [c.text.strip()[:500] for c in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
            return
        if not shape.has_text_frame:
            return
        paras = ["".join(run.text for run in para.runs).strip()
                 for para in shape.text_frame.paragraphs]
        paras = [x for x in paras if x]
        if not paras:
            return
        if kind == "title":
            if not title_seen[0]:
                lines.append("# " + paras[0])
                title_seen[0] = True
                lines.extend(paras[1:])
            else:
                lines.extend(paras)
        elif kind == "subtitle":
            lines.extend("### " + x for x in paras)
        else:
            lines.extend(paras)

    try:
        prs = Presentation(file_path)
        parts = []
        for i, slide in enumerate(prs.slides, 1):
            lines = []
            title_seen = [False]
            for shape in slide.shapes:
                _collect(shape, lines, title_seen)
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    lines.append(f"[演讲者备注] {notes[:2000]}")
            if lines:
                parts.append(f"## 幻灯片 {i}\n" + "\n".join(lines))
        return "\n\n".join(parts) if parts else None
    except Exception as e:
        messages.warn("warn_doc_error", doc="PowerPoint", err=e)
        return None