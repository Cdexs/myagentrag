#!/usr/bin/env python3
"""
智能内容提取工具 (smart-summarize)
功能：提取 YouTube/B站/网页/本地文件内容，不调用 LLM
支持格式：txt, md, pdf, docx, doc, epub, mp3, wav, mp4, mkv, 等

变更（节选）：
- v0.6: 模块化拆分——slicing.py（分片协议）/ extractors.py（提取器）/
  transcribe.py（whisper 转录）/ deps.py（依赖检测与安装）；
  extract.py 只保留 CLI 入口与调度
- v3.5: 运行时检测缺失组件（ffmpeg/whisper-cli/ggml 模型），提示大小并经用户确认后下载安装，随后继续原任务
- v3.4: 按操作系统寻找 whisper-cli，临时目录和 ffmpeg 查找跨平台化；cookies 改为显式环境变量
- v3.3: 音频转录只走 whisper.cpp，移除 faster-whisper 回退
"""

import sys
import json
import argparse
from pathlib import Path

from slicing import write_slices, SLICE_THRESHOLD_CHARS
from extractors import (
    detect_content_type, extract_video_id, extract_youtube, extract_bvid,
    extract_bilibili, extract_web, extract_text_file, extract_pdf_text,
    extract_word_text, extract_epub_text, extract_excel_text, extract_pptx_text,
)
from transcribe import extract_audio_text, extract_audio_srt, extract_video_text
import transcribe as _transcribe_mod
from deps import MissingDependencyError, _missing_pipelib_kinds, _dep_detail, _install_dep

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
    elif content_type == "excel":
        lib_groups.append("excel")
    elif content_type == "pptx":
        lib_groups.append("pptx")
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
    if content_type in ["text", "pdf", "word", "excel", "pptx", "epub", "audio", "video"]:
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
    parser.add_argument('--slice', type=int, metavar='N',
                        help='大文档模式下仅输出第 N 片内容（配合清单使用）')
    parser.add_argument('--download-deps', action='store_true',
                        help='缺组件时跳过交互确认，直接下载安装（用于 agent 代为确认后调用）')
    args = parser.parse_args()
    _transcribe_mod.NO_GPU = args.no_gpu

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
