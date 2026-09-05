#!/usr/bin/env python3
"""
智能内容提取工具 (smart-summarize)
功能：提取 YouTube/B站/网页/本地文件内容，不调用 LLM
支持格式：txt, md, pdf, docx, doc, epub, mp3, wav, mp4, mkv, 等

变更（节选）：
- v0.6: 模块化拆分——slicing.py（分片协议）/ extractors.py（提取器）/
  transcribe.py（whisper 转录）/ deps.py（组件检测与安装）/
  messages.py（zh/en 双语反馈，--lang 覆盖 + locale 自动探测）/
  workspace.py（知识库：SQLite FTS5 检索 / 时间戳索引 / 定位回放）/
  runtime.py（专用 Python 运行时：与用户系统环境彻底解耦，方案 v1.4 §8B）；
  extract.py 只保留 CLI 入口、调度与运行时闸门
- v3.5: 运行时检测缺失组件（ffmpeg/whisper-cli/ggml 模型），提示大小并经用户确认后下载安装，随后继续原任务
- v3.4: 按操作系统寻找 whisper-cli，临时目录和 ffmpeg 查找跨平台化；cookies 改为显式环境变量
- v3.3: 音频转录只走 whisper.cpp，移除 faster-whisper 回退
"""

import sys
import os
import json
import argparse
import subprocess
from pathlib import Path

from slicing import write_slices, SLICE_THRESHOLD_CHARS
from extractors import (
    detect_content_type, extract_video_id, extract_youtube, extract_bvid,
    extract_bilibili, extract_web, extract_text_file, extract_pdf_text,
    extract_word_text, extract_epub_text, extract_excel_text, extract_pptx_text,
)
from transcribe import extract_audio_text, extract_audio_srt, extract_video_text
import transcribe as _transcribe_mod
from deps import MissingDependencyError, _dep_detail, _install_dep
import messages
import workspace
import runtime

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
      SMART_SUMMARIZE_NO_RUNTIME=1 时跳过（测试用）。
    """
    if os.environ.get("SMART_SUMMARIZE_NO_RUNTIME"):
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
    if args.search:
        return workspace.ws_search(W, args.search, all_workspaces=args.all_workspaces)
    if args.entry:
        return workspace.ws_read_entry(W, args.entry, chunk_no=args.chunk)
    if args.remove:
        return workspace.ws_remove_entry(W, args.remove, yes=args.yes)
    if args.verify:
        return workspace.ws_verify(W)
    if args.reindex:
        return workspace.ws_reindex(W)
    if args.vacuum:
        return workspace.ws_vacuum(W)
    if args.play:
        return workspace.ws_play(W, args.play, args.at, duration=args.duration)
    return None


def _maybe_ingest(args, result):
    """提取成功且带 --workspace 时入库（§5）。返回追加 workspace 信息的 result。"""
    if not args.workspace or not result.get("success"):
        return result
    ct = detect_content_type(args.url or args.file)
    source_ref = args.url if args.url else (str(args.file) if args.file else None)
    kwargs = {
        "title": args.title or result.get("title") or result.get("filename"),
        "source_ref": source_ref,
        "author": args.author or result.get("author") or None,
        "publisher": args.publisher,
        "publish_date": args.publish_date,
        "keep_source": not args.no_keep_source,
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
        kwargs.update(source_type=ct, content=result.get("content"), source_file=args.file)

    ing = workspace.ws_ingest(args.workspace, **kwargs)
    if ing.get("success"):
        result["workspace"] = {"name": args.workspace, "entry_id": ing["entry_id"],
                               "chunk_count": ing["chunk_count"],
                               "total_chars": ing["total_chars"], "updated": ing["updated"]}
        print(f"  📥 {ing['message']}", file=sys.stderr)
    else:
        result["workspace_error"] = ing
    return result


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

def _handle_missing_deps(args, err):
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
                ans = input(messages.msg("deps_prompt"))
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
    return _run_extraction(args)

def main():
    parser = argparse.ArgumentParser(description='智能内容提取工具')
    parser.add_argument('--url', help='要提取的 URL')
    parser.add_argument('--file', help='要提取的本地文件')
    parser.add_argument('--output', choices=['json', 'text', 'srt'], default='json', help='输出格式 (srt 仅支持音频/视频转字幕)')
    parser.add_argument('--model', default='large-v3-turbo', help='Whisper 模型名称 (默认: large-v3-turbo; 可选 large-v3-turbo-q5_0 快速档)')
    parser.add_argument('--no-gpu', action='store_true', help='强制 CPU 转录（禁用 GPU 后端）')
    parser.add_argument('--slice', type=int, metavar='N',
                        help='大文档模式下仅输出第 N 片内容（配合清单使用）')
    parser.add_argument('--download-deps', action='store_true',
                        help='缺组件时跳过交互确认，直接下载安装（用于 agent 代为确认后调用）')
    parser.add_argument('--lang', choices=['zh', 'en'], default=None,
                        help='反馈语言 (默认: 按系统语言自动探测，可用 SMART_SUMMARIZE_LANG 覆盖)')
    # ---- 知识库 workspace（v1.3 §4 管理操作全集） ----
    parser.add_argument('--workspace', metavar='名', help='知识库 workspace 名称（提取时带上即入库）')
    parser.add_argument('--create', action='store_true', help='显式创建 workspace')
    parser.add_argument('--workspace-list', action='store_true', help='列举全部 workspace（名称/条目数/总字符数/最后更新）')
    parser.add_argument('--delete-workspace', action='store_true', help='删除整个 workspace 目录（需 --yes 二次确认）')
    parser.add_argument('--rename', metavar='新名', help='重命名 workspace')
    parser.add_argument('--stats', action='store_true', help='workspace 统计（条目/字符/分片/来源分布）')
    parser.add_argument('--list', action='store_true', help='列举 workspace 条目')
    parser.add_argument('--search', metavar='查询', help='FTS5 检索（支持 AND/OR/NOT/NEAR/前缀*，短语加引号）')
    parser.add_argument('--all-workspaces', action='store_true', help='跨全部 workspace 检索（与 --search 搭配）')
    parser.add_argument('--entry', metavar='ID', help='读取条目 full.md 全文')
    parser.add_argument('--chunk', type=int, metavar='N', help='配合 --entry 读取指定分片')
    parser.add_argument('--remove', metavar='ID', help='删除条目（需 --yes 二次确认）')
    parser.add_argument('--verify', action='store_true', help='完整性校验（片数/逐片一致性/覆盖/FTS 行数）')
    parser.add_argument('--reindex', action='store_true', help='重建 FTS 索引')
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
    messages.init(args.lang)
    _transcribe_mod.NO_GPU = args.no_gpu

    # 专用运行时闸门（v1.4 §8B）：缺失则引导安装，就绪则透明 re-exec（v1.4 决策 3：不回退用户环境）
    _runtime_gate(args)

    if args.chunk and not args.entry:
        parser.error(messages.msg("chunk_needs_entry"))

    ws_mgmt = any([args.workspace_list, args.create, args.delete_workspace, args.rename,
                   args.stats, args.list, args.search, args.entry, args.remove,
                   args.verify, args.reindex, args.vacuum, args.play])
    if ws_mgmt or args.workspace:
        _fts_gate(args)
    if ws_mgmt:
        result = _run_workspace_ops(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("success") else 1)

    if not args.url and not args.file:
        print(messages.msg("no_input"), file=sys.stderr)
        sys.exit(1)

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
        result = write_slices(title, args.file, result["content"],
                              args_slice=args.slice)
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
