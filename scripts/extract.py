#!/usr/bin/env python3
"""
MyAgentRAG — 本地知识库构建与检索工具
功能：把 YouTube/B站/网页/本地文件/音视频转录内容提取并入库到知识库 workspace，不调用 LLM
支持格式：txt, md, pdf, docx, doc, epub, mp3, wav, mp4, mkv, 等
"""

import sys
import os
import json
import argparse
import subprocess
from pathlib import Path

from slicing import write_slices, SLICE_THRESHOLD_CHARS
import extractors as _extractors_mod
from extractors import (
    detect_content_type, extract_video_id, extract_youtube, extract_bvid,
    extract_bilibili, extract_web, extract_text_file, extract_pdf_text,
    extract_word_text, extract_epub_text, extract_excel_text, extract_pptx_text,
)
from transcribe import extract_audio_text, extract_audio_srt, extract_video_text
import transcribe as _transcribe_mod
from deps import MissingDependencyError, _dep_detail, _install_dep
import deps
import messages
import workspace
import runtime
import embeddings

# Windows 管道输出默认 GBK，emoji/特殊字符会 UnicodeEncodeError 崩溃，强制 UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ==================== 本地文件分派 ====================

def extract_local_file(file_path, output_format='json', model='large-v3-turbo'):
    """根据文件类型自动选择提取方法（文档 → extractors，音视频 → transcribe）"""
    path = Path(file_path)
    if not path.exists():
        return messages.err_result("file_not_found", path=file_path)

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
    elif ext in ['.xlsx', '.xlsm']:
        content = extract_excel_text(file_path)
    elif ext == '.pptx':
        content = extract_pptx_text(file_path)
    elif ext in ['.mp3', '.wav', '.aac', '.m4a', '.flac', '.ogg', '.wma']:
        # 支持 SRT 格式输出
        if output_format == 'srt':
            content = extract_audio_srt(file_path, model=model)
        else:
            content = extract_audio_text(file_path, model=model)
    elif ext in ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm']:
        content = extract_video_text(file_path)

    # 结构化提取器返回 Extraction{text, srcmap}；普通提取器返回 str（无账本）
    if isinstance(content, _extractors_mod.Extraction):
        result["srcmap"] = content.srcmap
        content = content.text
    if content:
        result["content"] = content
        result["success"] = True
    else:
        result["error"], result["error_i18n"] = messages.err_field("cannot_extract", ext=ext)

    return result


# ==================== 专用运行时闸门（方案 v1.4 §8B） ====================

def _switch_to_runtime(rt):
    """以专用解释器重跑本脚本（透传全部参数），退出码透传。"""
    print(messages.msg("runtime_switch", python=rt), file=sys.stderr)
    proc = subprocess.run([str(rt), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit(proc.returncode)


def _runtime_gate(args):
    """v1.4 §8B：技能只使用专用 Python 运行时，与用户系统环境彻底解耦。

    - 已就绪且为当前进程 → 通过；
    - 已就绪但当前是引导层 → 透明 re-exec；
    - 缺失 → 复用组件确认 UI 引导安装（不回退用户环境），
      MYAGENTRAG_NO_RUNTIME=1 时跳过（测试用）。
    """
    if os.environ.get("MYAGENTRAG_NO_RUNTIME"):
        return
    r = runtime.ensure_runtime(allow_install=args.download_deps)
    if r["status"] == "ok-current":
        return
    if r["status"] in ("ok-switch", "install-attempt"):
        _switch_to_runtime(r["python"])
    # 缺失：走组件确认 UI（_handle_missing_deps），装好后由其信号切回本函数
    result = _handle_missing_deps(args, MissingDependencyError(["runtime"]))
    if result.get("_runtime_installed"):
        _switch_to_runtime(runtime.find_runtime_python())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1)


# ==================== 知识库依赖链闸门（方案 v1.5 §8C.11） ====================

def _kb_gate(args):
    """知识库功能前置：嵌入引擎 + 向量模型缺失 → 复用组件确认 UI 引导安装（全链一次列清）。
    --no-embed 入库 / --mode fts 检索不依赖向量组件，跳过本闸门。"""
    kinds = deps._missing_kb_kinds(embeddings.DEFAULT_MODEL)
    if not kinds:
        return
    result = _handle_missing_deps(args, MissingDependencyError(kinds),
                                  after_install=lambda: {"success": True,
                                                         "_kb_installed": True})
    if not result.get("_kb_installed"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)


# ==================== 知识库 workspace（v1.3 §1/§4/§5） ====================

def _fts_gate(args):
    """FTS5/trigram 安全网：专用运行时保证 trigram 可用，此检查正常情况下恒通过。"""
    ok, info = workspace.check_fts_env()
    if ok:
        return
    err = messages.err_result("env_fts_missing")
    err.update({"capability": "SQLite FTS5 + trigram tokenizer (SQLite >= 3.34)",
                "current": info,
                "hint": messages.msg("env_fts_hint"),
                "hint_i18n": messages.msg_pair("env_fts_hint")})
    print(json.dumps(err, ensure_ascii=False, indent=2))
    sys.exit(1)


def _run_workspace_ops(args):
    """§4 管理操作全集（13 项）。返回 result dict；无匹配操作返回 None。"""
    W = args.workspace
    if args.workspace_list:
        return workspace.list_workspaces()
    needs_ws = (args.create or args.delete_workspace or args.rename or args.stats
                or args.list or args.entry or args.remove or args.verify
                or args.reindex or args.vacuum or args.play
                or (args.search and not args.all_workspaces))
    if needs_ws and not W:
        return messages.err_result("ws_name_required")
    if args.create:
        return workspace.create_workspace(W)
    if args.delete_workspace:
        return workspace.delete_workspace(W, yes=args.yes)
    if args.rename:
        return workspace.rename_workspace(W, args.rename)
    if args.stats:
        return workspace.ws_stats(W)
    if args.list:
        return workspace.ws_list_entries(W)
    if args.search is not None:
        return workspace.ws_search(W, args.search, all_workspaces=args.all_workspaces,
                                   mode=args.mode, no_embed=args.no_embed,
                                   limit=min(max(args.limit, 1), 100))
    if args.section:
        return workspace.ws_read_entry(W, None, section=args.section,
                                       max_chars=max(0, args.max_chars))
    if args.entry:
        return workspace.ws_read_entry(W, args.entry, chunk_no=args.chunk,
                                       max_chars=max(0, args.max_chars))
    if args.remove:
        return workspace.ws_remove_entry(W, args.remove, yes=args.yes)
    if args.verify:
        return workspace.ws_verify(W)
    if args.embed:
        return workspace.ws_embed(W)
    if args.reindex:
        return workspace.ws_reindex(W)
    if args.vacuum:
        return workspace.ws_vacuum(W)
    if args.play:
        return workspace.ws_play(W, args.play, args.at, duration=args.duration)
    return None


def _ingest_kwargs(args, result):
    """提取结果 → ws_ingest/ws_ingest_batch 的 kwargs（按内容类型装配
    segments/srt/srcmap；title/author 等元数据缺省沿用提取结果）。"""
    ct = detect_content_type(args.url or args.file)
    source_ref = args.url if args.url else (str(args.file) if args.file else None)
    fallback_title = result.get("title") or (Path(result["filename"]).stem
                                             if result.get("filename") else None)
    kwargs = {
        "title": args.title or fallback_title,
        "source_ref": source_ref,
        "author": args.author or result.get("author") or None,
        "publisher": args.publisher,
        "publish_date": args.publish_date,
        "keep_source": not args.no_keep_source,
        "no_embed": args.no_embed,
    }
    if ct == "youtube":
        raw = result.get("raw_subtitle")
        ext = result.get("raw_subtitle_ext")
        segs = None
        if raw:
            segs = workspace.parse_vtt(raw) if ext == "vtt" else workspace.parse_srt(raw)
        kwargs.update(source_type="youtube", segments=segs or None,
                      raw_subtitle=raw, raw_subtitle_ext=ext,
                      content=result.get("transcript") or None)
    elif ct == "bilibili":
        kwargs.update(source_type="bilibili", segments=result.get("subtitle_segments"),
                      raw_subtitle=result.get("raw_subtitle"), raw_subtitle_ext=result.get("raw_subtitle_ext"),
                      content=result.get("transcript") or None)
    elif ct == "web":
        kwargs.update(source_type="web", content=result.get("content"))
    elif ct in ("audio", "video"):
        kwargs.update(source_type=ct, srt_text=result.get("_srt"), source_file=args.file)
    else:
        kwargs.update(source_type=ct, content=result.get("content"), source_file=args.file,
                      srcmap=result.get("srcmap"), replace=args.replace)
    return kwargs


def _maybe_ingest(args, result):
    """提取成功且带 --workspace 时入库（§5）。返回追加 workspace 信息的 result。"""
    if not args.workspace or not result.get("success"):
        return result
    kwargs = _ingest_kwargs(args, result)
    kwargs.setdefault("replace", False)   # 原契约：仅文档类型分支携带 --replace
    ing = workspace.ws_ingest(args.workspace, **kwargs)
    if ing.get("success"):
        result["workspace"] = {"name": args.workspace, "entry_id": ing["entry_id"],
                               "title": ing.get("title"),
                               "chunk_count": ing["chunk_count"], "vectors": ing["vectors"],
                               "total_chars": ing["total_chars"], "updated": ing["updated"],
                               "supersedes": ing.get("supersedes") or [],
                               "replaced": ing.get("replaced", False),
                               "message": ing.get("message"),
                               "message_i18n": ing.get("message_i18n")}
        if not args.quiet:          # P6：--quiet 抑制进度（supersedes 已在 JSON 内，stderr 提示冗余）
            if ing.get("supersedes"):
                print(messages.msg("ingest_supersedes",
                                   ids=", ".join(ing["supersedes"])), file=sys.stderr)
            else:
                print(f"  📥 {ing['message']}", file=sys.stderr)
    else:
        result["workspace_error"] = ing
    return result


# ==================== 批量入库（多文件合并嵌入） ====================

# --dir 扫描的受支持扩展名（与 SKILL.md「支持的内容源」一致）
_DIR_EXTS = {".txt", ".md", ".markdown", ".rst", ".csv", ".pdf", ".docx", ".doc",
             ".epub", ".xlsx", ".xlsm", ".pptx",
             ".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".wma",
             ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"}


def _scan_dir_files(d):
    """--dir 扫描：目录下受支持扩展名的文件（不含子目录），按文件名排序。"""
    p = Path(d)
    return sorted((str(f) for f in p.iterdir()
                   if f.is_file() and f.suffix.lower() in _DIR_EXTS),
                  key=lambda s: s.lower())


def _run_batch(args, files):
    """批量模式：逐文件提取 → 一次 ws_ingest_batch（合并嵌入，N×1.2s → 1×1.2s）。

    单文件提取失败跳过并记入 results（不阻塞其余）；合并嵌入失败 → 整批不入库
    （与单文件"嵌入失败未入库"语义一致）；单文件提取异常按文件隔离（批量容错）。
    输出恒为 JSON（--output text/srt 不适用于批量）。"""
    import copy
    import traceback
    items, results, item_pos = [], [], []
    for f in files:
        entry = {"file": f}
        a = copy.copy(args)
        a.file = f
        try:
            if not Path(f).exists():
                raise FileNotFoundError(str(f))
            try:
                r = _run_extraction(a)
            except MissingDependencyError as e:
                r = _handle_missing_deps(a, e)
            if not r.get("success"):
                entry["success"] = False
                entry["error"] = r.get("error") or messages.msg("unknown_error")
                if r.get("error_i18n"):
                    entry["error_i18n"] = r["error_i18n"]
                results.append(entry)
                continue
            if not args.workspace:
                entry["success"] = True
                entry["title"] = r.get("title")
                results.append(entry)
                continue
            items.append(_ingest_kwargs(a, r))
            item_pos.append(len(results))
            results.append(entry)            # 占位：入库结果回填
        except Exception as e:               # 批量容错：单文件异常不拖垮整批
            print(traceback.format_exc(), file=sys.stderr)
            entry["success"] = False
            entry["error"] = str(e) or e.__class__.__name__
            results.append(entry)

    ing = None
    if items and args.workspace:
        try:
            ing = workspace.ws_ingest_batch(args.workspace, items=items,
                                            no_embed=args.no_embed, replace=args.replace)
        except Exception as e:   # OPT-02：批量入库阶段任何异常都转结构化 JSON（KB-OUT-01）
            import traceback
            print(traceback.format_exc(), file=sys.stderr)   # 堆栈走 stderr，stdout 保持纯 JSON
            failed = [it.get("title") or it.get("source_file") or it.get("source_ref")
                      or f"item#{k + 1}" for k, it in enumerate(items)]
            return {"success": False, "batch": True, "workspace": args.workspace,
                    "error": str(e) or type(e).__name__,
                    "error_i18n": {"zh": f"批量入库失败: {e}",
                                   "en": f"Batch ingest failed: {e}"},
                    "results": results, "failed": failed, "embedded_windows": 0}
        for k, pos in enumerate(item_pos):
            res = ing["results"][k]
            entry = results[pos]
            if res is None or not res.get("success"):   # None 防御（ws_ingest_batch 契约保证非 None，双保险）
                entry["success"] = False
                entry["error"] = (res or {}).get("error") or "入库失败（无结构化结果）"
                if (res or {}).get("error_i18n"):
                    entry["error_i18n"] = res["error_i18n"]
                continue
            entry["success"] = True
            entry["title"] = res.get("title")
            entry["entry_id"] = res.get("entry_id")
            entry["chunk_count"] = res.get("chunk_count")
            entry["vectors"] = res.get("vectors")
            entry["updated"] = res.get("updated")
            entry["supersedes"] = res.get("supersedes") or []   # OPT-01：与单文件契约对齐
            entry["replaced"] = bool(res.get("replaced"))
    else:
        for pos in item_pos:                 # 无 --workspace：提取成功即成功
            results[pos]["success"] = True

    failed = [e["file"] for e in results if not e.get("success")]
    out = {"success": bool(results) and not failed, "batch": True,
           "workspace": args.workspace or None, "results": results,
           "failed": failed,
           "embedded_windows": (ing or {}).get("embedded_windows", 0)}
    if ing is not None and not ing.get("success"):
        # OPT-02：批量嵌入失败等错误透传顶层 error/error_i18n（KB-OUT-01 契约）
        out["error"] = ing.get("error") or "批量入库失败"
        out["error_i18n"] = ing.get("error_i18n") or {"zh": out["error"], "en": out["error"]}
    return out


# ==================== 主函数 ====================

def _run_extraction(args):
    target = args.url or args.file
    content_type = detect_content_type(target)

    # 扩展库随专用运行时预装（v1.4 §8B），无 pip 库预检；
    # ffmpeg/whisper-cli/ggml 组件缺失由 transcribe 路径抛 MissingDependencyError

    if content_type == "youtube":
        video_id = extract_video_id(args.url)
        return extract_youtube(video_id) if video_id else messages.err_result("video_id_fail")
    if content_type == "bilibili":
        bvid = extract_bvid(args.url)
        return extract_bilibili(bvid) if bvid else messages.err_result("bvid_fail")
    if content_type == "web":
        return extract_web(args.url)
    if content_type in ["text", "pdf", "word", "excel", "pptx", "epub", "audio", "video"]:
        if args.workspace and content_type in ("audio", "video"):
            # 入库的音视频统一走 whisper SRT 转录以获得时间戳索引（v1.3 §6.4）；
            # 不入库的普通提取行为不变（视频先试内置字幕）
            srt = extract_audio_srt(args.file, model=args.model)
            result = {"platform": "file", "filepath": str(Path(args.file)),
                      "filename": Path(args.file).name, "content": "",
                      "type": content_type, "success": False}
            if srt:
                segments = workspace.parse_srt(srt)
                result["content"], _ = workspace.text_from_segments(segments)
                result["success"] = True
                result["_srt"] = srt
            else:
                result["error"], result["error_i18n"] = messages.err_field(
                    "cannot_extract", ext=f".{content_type}")
            return result
        return extract_local_file(args.file, output_format=args.output, model=args.model)
    return messages.err_result("unsupported_type", t=content_type)

def _handle_missing_deps(args, err, after_install=None):
    """列出缺失组件（名称/用途/来源/预计大小），经用户确认后下载安装并继续原任务。"""
    items = [_dep_detail(k) for k in err.kinds]
    print(messages.msg("deps_header"), file=sys.stderr)
    for it in items:
        print(f"  • {it['name']}（{it['purpose']}）", file=sys.stderr)
        print(f"    {messages.msg('deps_item_source')}: {it['source']}", file=sys.stderr)
        print(f"    {messages.msg('deps_item_size')}: {it['est_size']}", file=sys.stderr)

    allowed = args.download_deps
    if not allowed:
        interactive = False
        try:
            interactive = bool(sys.stdin and sys.stdin.isatty())
        except Exception:
            interactive = False
        if not interactive:
            print(messages.msg("deps_noninteractive"), file=sys.stderr)
        else:
            try:
                # 提示语走 stderr：stdout 保持纯 JSON，不污染 agent 管道输出
                print(messages.msg("deps_prompt"), file=sys.stderr, end="")
                ans = input()
                allowed = ans.strip().lower() in ("y", "yes")
            except (EOFError, KeyboardInterrupt, OSError):
                allowed = False

    if not allowed:
        return messages.err_result("deps_declined") | {"missing": items}

    all_ok = True
    for it in items:
        try:
            path = _install_dep(it["kind"])
            print(messages.msg("deps_installed", name=it["name"], path=path), file=sys.stderr)
        except Exception as e:
            all_ok = False
            print(messages.msg("deps_install_fail", name=it["name"], err=e), file=sys.stderr)
    if not all_ok:
        return messages.err_result("deps_partial_fail") | {"missing": items}
    print(messages.msg("deps_ready"), file=sys.stderr)
    if "runtime" in err.kinds:
        # 引导层装完运行时不能继续提取（扩展库在专用解释器里），交由闸门 re-exec
        return {"success": True, "_runtime_installed": True}
    if after_install is not None:
        return after_install()
    return _run_extraction(args)

class _JsonArgParser(argparse.ArgumentParser):
    """L1/N6：argparse 参数误用同样走 JSON 契约（stdout 可 json.loads，rc=2）"""
    def error(self, message):
        print(json.dumps(messages.err_result("cli_args_error", err=message),
                         ensure_ascii=False))
        sys.exit(2)


def main():
    parser = _JsonArgParser(description='MyAgentRAG — 本地知识库构建与检索工具')
    parser.add_argument('--url', help='要提取的 URL')
    parser.add_argument('--file', action='append', metavar='文件',
                        help='要提取的本地文件（可重复：多文件批量入库，合并为一次嵌入调用）')
    parser.add_argument('--dir', metavar='目录',
                        help='批量入库：扫描目录下受支持的文件（不含子目录，按文件名排序）')
    parser.add_argument('--output', choices=['json', 'text', 'srt'], default='json', help='输出格式 (srt 仅支持音频/视频转字幕)')
    parser.add_argument('--model', default='large-v3-turbo', help='Whisper 模型名称 (默认: large-v3-turbo; 可选 large-v3-turbo-q5_0 快速档)')
    parser.add_argument('--no-gpu', action='store_true', help='强制 CPU 转录（禁用 GPU 后端）')
    parser.add_argument('--slice', type=int, metavar='N',
                        help='大文档模式下仅输出第 N 片内容（配合清单使用）')
    parser.add_argument('--download-deps', action='store_true',
                        help='缺组件时跳过交互确认，直接下载安装（用于 agent 代为确认后调用）')
    parser.add_argument('--lang', choices=['zh', 'en'], default=None,
                        help='反馈语言 (默认: 按系统语言自动探测，可用 MYAGENTRAG_LANG 覆盖)')
    # ---- 知识库 workspace（v1.3 §4 管理操作全集） ----
    parser.add_argument('--workspace', metavar='名', help='知识库 workspace 名称（提取时带上即入库）')
    parser.add_argument('--create', action='store_true', help='显式创建 workspace')
    parser.add_argument('--workspace-list', action='store_true', help='列举全部 workspace（名称/条目数/总字符数/最后更新）')
    parser.add_argument('--delete-workspace', action='store_true', help='删除整个 workspace 目录（需 --yes 二次确认）')
    parser.add_argument('--rename', metavar='新名', help='重命名 workspace')
    parser.add_argument('--stats', action='store_true', help='workspace 统计（条目/字符/分片/来源分布）')
    parser.add_argument('--list', action='store_true', help='列举 workspace 条目')
    parser.add_argument('--search', metavar='查询', help='FTS5 检索（支持 AND/OR/NOT/NEAR/前缀*，短语加引号）')
    parser.add_argument('--mode', choices=['fused', 'fts', 'vector'], default='fused',
                        help='检索模式（默认 fused：FTS+向量 RRF 融合；fts=仅关键词；vector=仅语义）')
    parser.add_argument('--no-embed', action='store_true',
                        help='本次不做向量嵌入（入库仅建 FTS 索引；检索仅走 FTS 路）')
    parser.add_argument('--all-workspaces', action='store_true', help='跨全部 workspace 检索（与 --search 搭配）')
    parser.add_argument('--limit', type=int, default=20, metavar='N', help='检索返回条数上限（1..100，默认 20）')
    parser.add_argument('--quiet', action='store_true',
                        help='抑制 stderr 进度行（合并 2>&1 管道解析 JSON 时使用）')
    parser.add_argument('--max-chars', type=int, default=30000, metavar='N',
                        help='读取内容上限（字符；0=不限；默认 30000，超出截断并标注 truncated/remaining_chars）')
    parser.add_argument('--entry', metavar='ID', help='读取条目 full.md 全文')
    parser.add_argument('--chunk', type=int, metavar='N', help='配合 --entry 读取指定分片')
    parser.add_argument('--section', metavar='REF', help='精读检索返回的章节引用（如 e12ab34d#s5）')
    parser.add_argument('--remove', metavar='ID', help='删除条目（需 --yes 二次确认）')
    parser.add_argument('--verify', action='store_true', help='完整性校验（片数/逐片一致性/覆盖/FTS 行数）')
    parser.add_argument('--reindex', action='store_true', help='重建 FTS+标题锚点索引（不含向量）')
    parser.add_argument('--embed', action='store_true', help='为库内零向量条目补建向量（需嵌入链）')
    parser.add_argument('--replace', action='store_true', help='重入库同源（source_ref 相同）时自动删除旧条目')
    parser.add_argument('--vacuum', action='store_true', help='VACUUM 压缩 db')
    parser.add_argument('--play', metavar='ID', help='定位回放音视频条目（调用外部播放器）')
    parser.add_argument('--at', metavar='mm:ss', help='回放起点（mm:ss / hh:mm:ss / 秒数）')
    parser.add_argument('--duration', type=int, metavar='秒', help='回放时长（秒，可选）')
    parser.add_argument('--no-keep-source', action='store_true', help='入库时不保存来源文件副本')
    parser.add_argument('--yes', action='store_true', help='跳过删除类操作的二次确认（agent 已向用户确认后使用）')
    parser.add_argument('--title', help='入库条目标题（默认自动取自内容）')
    parser.add_argument('--author', help='入库作者')
    parser.add_argument('--publisher', help='入库出版社/发布方')
    parser.add_argument('--publish-date', help='入库出版/发布时间（ISO 8601，可只到年）')
    args = parser.parse_args()
    # @库名 约定：用户提示词里 @库名 / "在X库"等说法由 agent 解析库名；
    # CLI 对 --workspace 的 @ 前缀自动剥离，--workspace "@库名" 原样传入即可用
    if args.workspace:
        args.workspace = args.workspace.strip().lstrip("@").strip()
    messages.init(args.lang)
    _transcribe_mod.NO_GPU = args.no_gpu

    # 专用运行时闸门（v1.4 §8B）：缺失则引导安装，就绪则透明 re-exec（v1.4 决策 3：不回退用户环境）
    _runtime_gate(args)

    # 知识库依赖链闸门（v1.5 §8C.11）：嵌入引擎/向量模型缺失 → 全链确认安装
    needs_embed = bool(
        (args.search and args.mode in ("fused", "vector"))
        or args.embed
        or (args.workspace and (args.url or args.file) and not args.no_embed))
    if needs_embed and not os.environ.get("MYAGENTRAG_NO_RUNTIME"):
        _kb_gate(args)

    if args.chunk and not args.entry:
        parser.error(messages.msg("chunk_needs_entry"))

    ws_mgmt = any([args.workspace_list, args.create, args.delete_workspace, args.rename,
                   args.stats, args.list, args.search is not None, args.entry, args.section,
                   args.embed, args.remove, args.verify, args.reindex, args.vacuum,
                   args.play])
    if ws_mgmt or args.workspace:
        _fts_gate(args)
    if ws_mgmt:
        result = _run_workspace_ops(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("success") else 1)

    # 批量输入归集：--file 可重复 + --dir 目录扫描（单输入 → 原路径，零行为变化）
    files = list(args.file or [])
    if args.dir:
        if not Path(args.dir).is_dir():
            print(json.dumps(messages.err_result("dir_not_found", path=args.dir),
                             ensure_ascii=False))
            sys.exit(1)
        files.extend(_scan_dir_files(args.dir))
    if args.url and len(files) > 1:
        print(json.dumps(messages.err_result("url_batch_conflict"), ensure_ascii=False))
        sys.exit(1)
    if args.dir and not files:
        print(json.dumps(messages.err_result("dir_empty", path=args.dir), ensure_ascii=False))
        sys.exit(1)

    # --dir 恒为 batch 形态（KB-OPT-42：调用方只需解析一种契约，即使只扫到 1 个文件）
    batch_mode = not args.url and (len(files) > 1 or bool(args.dir))

    if not args.url and not files:
        # 无任务输入同样走 JSON 契约（S10：失败路径全部 json.loads 可解析）
        print(json.dumps(messages.err_result("no_input"), ensure_ascii=False))
        sys.exit(1)

    if batch_mode:
        # 批量模式：逐文件提取 + 一次合并嵌入入库（输出恒为 JSON）
        args.file = files
        result = _run_batch(args, files)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("success") else 1)

    args.file = files[0] if files else None  # 单输入：归一化回 str，原路径零行为变化

    # 本地文件不存在时提前报错（否则会被当成 unknown 类型，报错误导人）
    if args.file and not Path(args.file).exists():
        print(json.dumps(messages.err_result("file_not_found", path=args.file), ensure_ascii=False))
        sys.exit(1)

    try:
        result = _run_extraction(args)
    except MissingDependencyError as e:
        result = _handle_missing_deps(args, e)

    result = _maybe_ingest(args, result)

    if (args.output == 'json' and result.get("success")
            and isinstance(result.get("content"), str)
            and len(result["content"]) > SLICE_THRESHOLD_CHARS):
        # slice protocol：超过阈值的内容分片落盘，stdout 只输出清单——
        # agent 按清单逐片读取（塞爆上下文的物理上限被提取器锁死）
        title = result.get("title") or result.get("filename") or str(args.file)
        _ws_info = result.get("workspace")   # 分片覆盖前保留入库信息（N5 深层根因）
        result = write_slices(title, args.file, result["content"],
                              args_slice=args.slice)
        if _ws_info:
            result["workspace"] = _ws_info
    if args.output == 'json':
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.output == 'srt':
        # SRT 直接输出内容
        if result.get("success"):
            print(result.get('content', ''))
        else:
            print(messages.msg("extract_fail",
                               err=result.get('error') or messages.msg("unknown_error")), file=sys.stderr)
            sys.exit(1)
    else:
        # text 格式
        if result.get("success"):
            print(f"标题: {result.get('title', result.get('filename', ''))}")
            if result.get('author'):
                print(f"作者: {result['author']}")
            print(f"\n内容:\n{result.get('transcript', result.get('content', ''))}")
        else:
            print(messages.msg("extract_fail",
                               err=result.get('error') or messages.msg("unknown_error")), file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
