# -*- coding: utf-8 -*-
"""双语反馈消息模块（方案 docs/kb-sqlite-fts5-design-v1.3.md §8A）

集中管理全部面向用户的反馈字符串（zh/en），避免文案散落：

- 语言优先级：``--lang`` 显式参数 > 环境变量 MYAGENTRAG_LANG > 系统语言自动探测 > zh
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
    "web_fetch_failed": {
        "zh": "网页抓取失败（HTTP {status}）：{url}",
        "en": "Web fetch failed (HTTP {status}): {url}",
    },
    "web_fetch_error": {
        "zh": "网页抓取异常：{err}",
        "en": "Web fetch error: {err}",
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
    # ---- workspace.py：管理与检索反馈（v1.3 §8A 实施范围） ----
    "ws_name_invalid": {
        "zh": "workspace 名称只能包含字母/数字/中文/-/_：{name}",
        "en": "workspace name may only contain letters/digits/CJK/-/_: {name}",
    },
    "ws_not_found": {
        "zh": "workspace 不存在: {name}",
        "en": "workspace not found: {name}",
    },
    "ws_exists": {
        "zh": "workspace 已存在: {name}",
        "en": "workspace already exists: {name}",
    },
    "ws_created": {
        "zh": "workspace 已创建: {name}",
        "en": "workspace created: {name}",
    },
    "ws_no_workspaces": {
        "zh": "尚无任何 workspace（用 --workspace <名> --create 创建，或提取时加 --workspace 隐式创建）",
        "en": "No workspaces yet (create one with --workspace <name> --create, or pass --workspace while extracting)",
    },
    "ws_renamed": {
        "zh": "workspace 已重命名: {old} → {new}",
        "en": "workspace renamed: {old} → {new}",
    },
    "ws_confirm_required": {
        "zh": "该操作不可逆，需要二次确认：加 --yes 后重试",
        "en": "This operation is irreversible; add --yes to confirm and retry",
    },
    "ws_entry_not_found": {
        "zh": "条目不存在: {eid}（workspace: {ws}）",
        "en": "Entry not found: {eid} (workspace: {ws})",
    },
    "ws_section_not_found": {
        "zh": "章节引用无效或条目无锚点: {ref}",
        "en": "Invalid section ref or entry has no anchors: {ref}",
    },
    "ws_embed_done": {
        "zh": "已为 {n} 个条目补建向量（{wins} 个窗口）",
        "en": "Rebuilt vectors for {n} entries ({wins} windows)",
    },
    "ws_embed_none": {
        "zh": "库内所有条目均已拥有向量，无需补建",
        "en": "All entries already have vectors, nothing to rebuild",
    },
    "ws_embed_fail": {
        "zh": "向量补建失败: {err}",
        "en": "Vector rebuild failed: {err}",
    },
    "cli_args_error": {
        "zh": "参数错误: {err}（用法与正确示例见 SKILL.md）",
        "en": "Invalid arguments: {err} (see SKILL.md for usage)",
    },
    "ingest_fullmd_mismatch": {
        "zh": "full.md 写入自检失败（内容校验和不符），已中止入库",
        "en": "full.md write self-check failed (checksum mismatch), ingest aborted",
    },
    "ws_vector_zero_hint": {
        "zh": "向量链路就绪但该库暂无向量索引（入库时可能使用了 --no-embed）；补建：--embed 或重入库不加减嵌入",
        "en": "Vector chain ready but no vectors indexed (ingested with --no-embed?); rebuild via --embed or re-ingest without it",
    },
    "ingest_supersedes": {
        "zh": "检测到同源旧条目 {ids}，可 --remove 清理或加 --replace 自动替换",
        "en": "Same-source old entries found: {ids}; clean via --remove <id> or rerun with --replace",
    },
    "ws_entry_removed": {
        "zh": "条目已删除: {eid}",
        "en": "Entry removed: {eid}",
    },
    "ws_verify_ok": {
        "zh": "完整性校验通过",
        "en": "Integrity verification passed",
    },
    "ws_reindexed": {
        "zh": "FTS 索引已重建",
        "en": "FTS index rebuilt",
    },
    "ws_vacuumed": {
        "zh": "VACUUM 完成",
        "en": "VACUUM completed",
    },
    "ws_search_empty": {
        "zh": "请提供 --search 查询词",
        "en": "A --search query is required",
    },
    "ws_range_only": {
        "zh": "仅元数据范围过滤（publish_date/created_at 配 >=/<=/>/<）无法排序检索，请附加主题词（浏览可用 --list）",
        "en": "Range-only filters (publish_date/created_at with >=/<=/>/<) cannot be ranked; add topic words (or use --list to browse)",
    },
    "dir_not_found": {
        "zh": "目录不存在: {path}",
        "en": "Directory not found: {path}",
    },
    "dir_empty": {
        "zh": "目录内无受支持的文件: {path}",
        "en": "No supported files in directory: {path}",
    },
    "play_uri_invalid": {
        "zh": "myagentrag:// 播放链接不合法: {err}",
        "en": "Invalid myagentrag:// play link: {err}",
    },
    "protocol_registered": {
        "zh": "myagentrag:// 协议已注册（仅 skill 自有命名空间，不影响系统默认播放器；--unregister-protocol 可移除）",
        "en": "myagentrag:// protocol registered (skill-owned namespace only; system default players untouched; remove with --unregister-protocol)",
    },
    "protocol_unregistered": {
        "zh": "myagentrag:// 协议已移除",
        "en": "myagentrag:// protocol removed",
    },
    "protocol_unsupported": {
        "zh": "当前平台暂不支持 myagentrag:// 协议注册: {os}",
        "en": "myagentrag:// protocol registration is not supported on this platform: {os}",
    },
    "protocol_register_failed": {
        "zh": "myagentrag:// 协议注册失败: {err}",
        "en": "myagentrag:// protocol registration failed: {err}",
    },
    "protocol_skill_dir_hint": {
        "zh": "技能目录已记录: {dir}——如移动技能目录，设置 MYAGENTRAG_SKILL_DIR 环境变量或重跑 --register-protocol 即可（注册项本身不写死该路径）",
        "en": "Skill directory recorded: {dir} — if you move it, set the MYAGENTRAG_SKILL_DIR environment variable or re-run --register-protocol (the registration itself does not hardcode this path)",
    },
    "url_batch_conflict": {
        "zh": "--url 与多文件批量入库不可同用（--url 只支持单输入）",
        "en": "--url cannot be combined with multi-file batch ingest (--url supports a single input)",
    },
    "ws_name_required": {
        "zh": "该操作需要 --workspace <名>",
        "en": "This operation requires --workspace <name>",
    },
    "chunk_needs_entry": {
        "zh": "--chunk N 需与 --entry <id> 搭配使用",
        "en": "--chunk N must be used together with --entry <id>",
    },
    "ingest_empty": {
        "zh": "提取内容为空，未入库",
        "en": "Extracted content is empty; nothing ingested",
    },
    "ingest_embed_fail": {
        "zh": "向量嵌入失败，内容未入库：{err}",
        "en": "Embedding failed; nothing ingested: {err}",
    },
    "ingest_ok": {
        "zh": "已入库 workspace[{ws}]：{title}（{chars} 字符 / {chunks} 片）",
        "en": "Ingested into workspace[{ws}]: {title} ({chars} chars / {chunks} chunks)",
    },
    "source_copy_warn": {
        "zh": "  ⚠️ 来源文件副本保存失败: {err}",
        "en": "  ⚠️ Failed to save the source file copy: {err}",
    },
    "play_no_player": {
        "zh": "无可用媒体播放器 / no media player found",
        "en": "no media player found",
    },
    "play_no_player_hint": {
        "zh": "建议安装 VLC 后重试",
        "en": "suggest installing VLC, then retry",
    },
    "play_no_media": {
        "zh": "该条目没有本地媒体文件副本（入库时可能使用了 --no-keep-source）",
        "en": "No local media file copy for this entry (it may have been ingested with --no-keep-source)",
    },
    "play_at_invalid": {
        "zh": "--at 时间格式应为 mm:ss / hh:mm:ss / 秒数: {v}",
        "en": "--at must be mm:ss / hh:mm:ss / seconds: {v}",
    },
    "play_launched": {
        "zh": "已调用播放器 {player}",
        "en": "Launched player {player}",
    },
    "play_degraded": {
        "zh": "系统默认方式打开，不支持定位（从头播放）",
        "en": "Opened with the system default app; seeking is not supported (plays from the start)",
    },
    "env_fts_missing": {
        "zh": "当前 Python 的 SQLite 缺少 FTS5/trigram 能力，workspace 功能不可用（不做降级检索）",
        "en": "This Python's SQLite lacks FTS5/trigram; workspace features unavailable (no degraded search)",
    },
    "env_fts_hint": {
        "zh": "安装任一 Python ≥3.9 官方构建后重试，技能会自动检测并切换",
        "en": "Install any official Python ≥3.9 build and retry; the skill will detect and switch automatically",
    },
    "env_fts_switch": {
        "zh": "  ↻ 当前解释器不支持 FTS5/trigram，切换到可用解释器重跑: {python}",
        "en": "  ↻ Current interpreter lacks FTS5/trigram, switching to a capable interpreter: {python}",
    },
    "env_fts_install_try": {
        "zh": "  ⬇ 未找到可用解释器，尝试为技能安装 Python（--download-deps 已确认）...",
        "en": "  ⬇ No capable interpreter found; installing Python for the skill (--download-deps confirmed)...",
    },
    # ---- runtime.py：专用 Python 运行时（方案 v1.4 §8B） ----
    "runtime_switch": {
        "zh": "  ↻ 切换到技能专用 Python 运行时: {python}",
        "en": "  ↻ Switching to the skill-dedicated Python runtime: {python}",
    },
    "dep_purpose_runtime": {
        "zh": "技能专用 Python 运行时（与系统 Python 彻底隔离，内置全部提取扩展库与 FTS5 检索引擎）",
        "en": "Skill-dedicated Python runtime (fully isolated from system Python; bundles all extraction libraries and the FTS5 search engine)",
    },
    "dep_source_runtime": {
        "zh": "python-build-standalone（CPython 3.12 独立构建，SHA256 校验）+ PyPI 锁定扩展库 → ~/.myagentrag/runtime/",
        "en": "python-build-standalone (standalone CPython 3.12, SHA256 verified) + pinned libraries from PyPI → ~/.myagentrag/runtime/",
    },
    "dep_size_runtime": {
        "zh": "约 150 MB 下载 / 约 350 MB 磁盘（以实际为准）",
        "en": "~150 MB download / ~350 MB disk (actual size prevails)",
    },
    "dep_purpose_llama_embed": {
        "zh": "向量检索嵌入引擎（CPU 推理，与 whisper-cli 同机制；独立子目录避免与 whisper 的 ggml DLL 冲突）",
        "en": "Vector-search embedding engine (CPU inference, same mechanism as whisper-cli; isolated subdirectory to avoid ggml DLL conflicts)",
    },
    "dep_source_llama_embed": {
        "zh": "GitHub Releases（llama.cpp {tag}，Windows zip / macOS brew / Linux 源码构建）→ ~/.myagentrag/bin/llama/",
        "en": "GitHub Releases (llama.cpp {tag}: Windows zip / macOS brew / Linux source build) → ~/.myagentrag/bin/llama/",
    },
    "dep_purpose_embedding_model": {
        "zh": "向量检索模型 {model}（中英文语义嵌入，本地推理）",
        "en": "Vector-search model {model} (zh/en semantic embeddings, local inference)",
    },
    "dep_source_embedding_model": {
        "zh": "HuggingFace（可配 MYAGENTRAG_HF_MIRROR 镜像）→ ~/.myagentrag/models/embedding/{model}/",
        "en": "HuggingFace (MYAGENTRAG_HF_MIRROR supported) → ~/.myagentrag/models/embedding/{model}/",
    },
    "dep_purpose_sqlite_vec": {
        "zh": "向量检索加速后端（SQLite C 扩展，库内 SIMD 扫描，降低内存占用）",
        "en": "Vector-search acceleration backend (SQLite C extension, in-DB SIMD scan, lower memory)",
    },
    "dep_source_sqlite_vec": {
        "zh": "PyPI（可配 MYAGENTRAG_PIP_INDEX_URL 镜像）/ GitHub Releases → 专用运行时 venv",
        "en": "PyPI (MYAGENTRAG_PIP_INDEX_URL supported) / GitHub Releases → dedicated runtime venv",
    },
}

_lang = None


def set_lang(lang):
    """显式设置当前语言（'zh'/'en'，大小写不敏感；其他值回落 zh）。"""
    global _lang
    _lang = "en" if str(lang or "").lower().startswith("en") else "zh"


def detect_lang():
    """系统语言自动探测：MYAGENTRAG_LANG → Windows UI 语言 → POSIX locale → zh。"""
    env = os.environ.get("MYAGENTRAG_LANG")
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
