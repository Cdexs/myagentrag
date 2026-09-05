# -*- coding: utf-8 -*-
"""双语反馈消息模块 — smart-summarize v0.6.0（T5，方案 docs/kb-sqlite-fts5-design-v1.3.md §8A）

集中管理全部面向用户的反馈字符串（zh/en），避免文案散落：

- 语言优先级：``--lang`` 显式参数 > 环境变量 SMART_SUMMARIZE_LANG > 系统语言自动探测 > zh
- JSON 失败结构：``error``（当前语言文案）+ ``error_i18n: {"zh": ..., "en": ...}``（双份，
  调用方 agent 可按自身界面语言选用）
- 原始异常文本（str(e)）不翻译，保持单语 error，不加 error_i18n
- 进度/状态类 stderr 行单语输出（msg/warn）；技能默认输出给 agent 消费，不直接弹 UI
"""
import os
import sys

SUPPORTED_LANGS = ("zh", "en")

MESSAGES = {
    # ---- extract.py：入参与分派 ----
    "no_input": {
        "zh": "错误：请提供 --url 或 --file",
        "en": "Error: --url or --file is required",
    },
    "file_not_found": {
        "zh": "文件不存在: {path}",
        "en": "File not found: {path}",
    },
    "video_id_fail": {
        "zh": "无法提取视频ID",
        "en": "Failed to extract video ID",
    },
    "bvid_fail": {
        "zh": "无法提取BV号",
        "en": "Failed to extract BV id",
    },
    "unsupported_type": {
        "zh": "不支持的内容类型: {t}",
        "en": "Unsupported content type: {t}",
    },
    "cannot_extract": {
        "zh": "无法提取 {ext} 文件内容",
        "en": "Cannot extract {ext} file content",
    },
    "extract_fail": {
        "zh": "提取失败: {err}",
        "en": "Extraction failed: {err}",
    },
    "unknown_error": {
        "zh": "未知错误",
        "en": "Unknown error",
    },
    # ---- extract.py：组件缺失确认与安装流程 ----
    "deps_header": {
        "zh": "\n⚠️ 提取/转录所需的以下组件缺失：",
        "en": "\n⚠️ The following components required for extraction/transcription are missing:",
    },
    "deps_noninteractive": {
        "zh": "\n（非交互环境：可在用户确认后加 --download-deps 重新运行）",
        "en": "\n(Non-interactive environment: re-run with --download-deps after user confirmation)",
    },
    "deps_prompt": {
        "zh": "\n是否立即下载并安装以上组件，然后继续任务? [y/N] ",
        "en": "\nDownload and install the components above now, then continue? [y/N] ",
    },
    "deps_declined": {
        "zh": "缺少必需组件，未下载。请确认后重试。",
        "en": "Required components are missing; nothing was downloaded. Confirm and retry.",
    },
    "deps_installed": {
        "zh": "  ✅ 已安装 {name} → {path}",
        "en": "  ✅ Installed {name} → {path}",
    },
    "deps_install_fail": {
        "zh": "  ❌ {name} 安装失败: {err}",
        "en": "  ❌ Failed to install {name}: {err}",
    },
    "deps_partial_fail": {
        "zh": "部分组件安装失败（见 stderr）。可手动安装后重试。",
        "en": "Some components failed to install (see stderr). Install them manually and retry.",
    },
    "deps_ready": {
        "zh": "  ↻ 组件就绪，继续执行原任务...",
        "en": "  ↻ Components ready, resuming the original task...",
    },
    "deps_item_source": {
        "zh": "来源",
        "en": "Source",
    },
    "deps_item_size": {
        "zh": "预计大小",
        "en": "Estimated size",
    },
    # ---- deps.py：组件清单描述（确认对话框用） ----
    "dep_purpose_pdf": {
        "zh": "PDF 文本提取（pdfplumber 或 PyMuPDF 任一即可）",
        "en": "PDF text extraction (pdfplumber or PyMuPDF, either one)",
    },
    "dep_purpose_docx": {
        "zh": "Word (.docx) 文本提取",
        "en": "Word (.docx) text extraction",
    },
    "dep_purpose_excel": {
        "zh": "Excel (.xlsx/.xlsm) 表格文本提取",
        "en": "Excel (.xlsx/.xlsm) spreadsheet text extraction",
    },
    "dep_purpose_pptx": {
        "zh": "PowerPoint (.pptx) 幻灯片文本提取",
        "en": "PowerPoint (.pptx) slide text extraction",
    },
    "dep_purpose_epub": {
        "zh": "EPUB 电子书文本提取",
        "en": "EPUB e-book text extraction",
    },
    "dep_purpose_yt-dlp": {
        "zh": "YouTube 字幕与元数据提取",
        "en": "YouTube subtitles and metadata extraction",
    },
    "dep_purpose_requests": {
        "zh": "网页正文与 B 站字幕提取",
        "en": "Web page text and Bilibili subtitle fetching",
    },
    "dep_unknown_group": {
        "zh": "Python 库",
        "en": "Python library",
    },
    "dep_purpose_ffmpeg": {
        "zh": "音视频解码与音频提取",
        "en": "Audio/video decoding and audio extraction",
    },
    "dep_purpose_whispercli": {
        "zh": "本地语音转录",
        "en": "Local speech transcription",
    },
    "dep_purpose_model": {
        "zh": "whisper 转录模型 ({model})",
        "en": "whisper transcription model ({model})",
    },
    "dep_source_pip": {
        "zh": "安装到当前 Python 解释器：\"{python}\" -m pip install {pkgs}",
        "en": "Install into the current Python interpreter: \"{python}\" -m pip install {pkgs}",
    },
    "dep_source_whispercli": {
        "zh": "依次尝试：brew 预编译包 → GitHub 官方预编译版 → 源码构建（需 git/cmake/编译器）",
        "en": "Try in order: brew prebuilt → GitHub official prebuilt → source build (needs git/cmake/compiler)",
    },
    "dep_size_pip": {
        "zh": "合计数 MB",
        "en": "a few MB total",
    },
    "dep_size_ffmpeg_est": {
        "zh": "约 100 MB（以实际下载为准）",
        "en": "~100 MB (actual download size prevails)",
    },
    "dep_size_whispercli": {
        "zh": "仓库约 60MB + 编译时间",
        "en": "~60MB repo + build time",
    },
    # ---- extractors.py ----
    "ytdlp_missing": {
        "zh": "未找到 yt-dlp，请安装依赖并确保其位于当前 Python 环境或 PATH",
        "en": "yt-dlp not found; install it into the current Python environment or PATH",
    },
    "bilibili_cookie_ok": {
        "zh": "  🍪 已携带 B站登录态（支持 AI 字幕等登录墙内容）",
        "en": "  🍪 Bilibili cookies attached (enables AI subtitles and login-walled content)",
    },
    "bilibili_cookie_bad": {
        "zh": "  ⚠️ B站 cookies 文件格式无法识别，已忽略: {path}",
        "en": "  ⚠️ Unrecognized Bilibili cookies file format, ignored: {path}",
    },
    "warn_no_lib": {
        "zh": "  ⚠️ 未安装 {lib}",
        "en": "  ⚠️ {lib} is not installed",
    },
    "warn_no_pdf_lib": {
        "zh": "  ⚠️ 未安装 pdfplumber 或 PyMuPDF",
        "en": "  ⚠️ Neither pdfplumber nor PyMuPDF is installed",
    },
    "warn_pandoc_missing": {
        "zh": "  ⚠️ pandoc 不可用",
        "en": "  ⚠️ pandoc is not available",
    },
    "warn_pdfplumber_fail": {
        "zh": "  ⚠️ pdfplumber 失败: {err}",
        "en": "  ⚠️ pdfplumber failed: {err}",
    },
    "warn_doc_error": {
        "zh": "  ⚠️ {doc}提取错误: {err}",
        "en": "  ⚠️ {doc} extraction error: {err}",
    },
    # ---- transcribe.py：状态与错误行 ----
    "trans_done_gpu": {
        "zh": "  🎮 转录完成（GPU 加速: {backend}）: {model}",
        "en": "  🎮 Transcription done (GPU accelerated: {backend}): {model}",
    },
    "trans_done_cpu_forced": {
        "zh": "  🖥 转录完成（已强制 CPU）: {model}",
        "en": "  🖥 Transcription done (CPU forced): {model}",
    },
    "trans_done_cpu": {
        "zh": "  ✅ 转录完成（CPU）: {model}",
        "en": "  ✅ Transcription done (CPU): {model}",
    },
    "transcribe_error": {
        "zh": "  ⚠️ whisper.cpp 转录错误: {err}",
        "en": "  ⚠️ whisper.cpp transcription error: {err}",
    },
    "video_error": {
        "zh": "  ⚠️ 视频处理错误: {err}",
        "en": "  ⚠️ Video processing error: {err}",
    },
}

_lang = None


def set_lang(lang):
    """显式设置当前语言（'zh'/'en'，大小写不敏感；其他值回落 zh）。"""
    global _lang
    _lang = "en" if str(lang or "").lower().startswith("en") else "zh"


def detect_lang():
    """系统语言自动探测：SMART_SUMMARIZE_LANG → Windows UI 语言 → POSIX locale → zh。"""
    env = os.environ.get("SMART_SUMMARIZE_LANG")
    if env and env[:2].lower() in SUPPORTED_LANGS:
        return env[:2].lower()
    if sys.platform == "win32":
        try:
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "zh" if (langid & 0xFF) == 0x04 else "en"
        except Exception:
            pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var)
        if v:
            return "zh" if v[:2].lower() == "zh" else "en"
    return "zh"


def get_lang():
    """当前语言；未初始化时自动探测一次。"""
    global _lang
    if _lang is None:
        set_lang(detect_lang())
    return _lang


def init(lang=None):
    """入口初始化：--lang 显式值优先，否则系统自动探测。"""
    set_lang(lang) if lang else set_lang(detect_lang())
    return get_lang()


def msg(key, **kw):
    """按当前语言取文案并格式化；未知 key 原样返回便于发现缺键。"""
    entry = MESSAGES.get(key)
    if not entry:
        return key
    return entry[get_lang()].format(**kw)


def msg_pair(key, **kw):
    """取双语两份格式化文案 {"zh": ..., "en": ...}。"""
    entry = MESSAGES.get(key)
    if not entry:
        return {"zh": key, "en": key}
    return {lang: entry[lang].format(**kw) for lang in SUPPORTED_LANGS}


def err_result(key, **kw):
    """构造双语失败结果 dict（success=False + error + error_i18n）。"""
    pair = msg_pair(key, **kw)
    return {"success": False, "error": pair[get_lang()], "error_i18n": pair}


def err_field(key, **kw):
    """返回 (当前语言文案, 双语 pair)，用于向既有 result dict 补 error/error_i18n。"""
    pair = msg_pair(key, **kw)
    return pair[get_lang()], pair


def warn(key, **kw):
    """向 stderr 输出单语提示行。"""
    print(msg(key, **kw), file=sys.stderr)
