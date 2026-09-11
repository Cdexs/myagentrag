# -*- coding: utf-8 -*-
"""大文档分片协议（slice protocol）

设计：<=SLICE_THRESHOLD_CHARS 走 stdout 直出；超过则分片落盘到受管临时目录，
stdout 只输出清单（<2KB），agent 按需读分片文件——塞爆上下文的物理上限被提取器锁死。
"""
import hashlib
import json
import re
from pathlib import Path

SLICE_THRESHOLD_CHARS = 256 * 1024
CHUNK_CHARS = 40000          # 每片字符数上限
CHUNK_OVERLAP_CHARS = 300    # 相邻片重叠窗口


def _default_temp_base_dir():
    """受管临时根目录：显式环境变量优先，其次受管目录 tmp/（与 bin/models/runtime
    同级的自管区域——不散落系统临时目录），系统临时目录仅作最后兜底。"""
    import os
    import tempfile
    configured = os.environ.get("MYAGENTRAG_TMPDIR")
    if configured:
        return Path(configured).expanduser()
    # 与 deps.MANAGED_HOME / extractors 同一解析（内联，避免 slicing←deps 循环导入）
    home = Path(os.environ.get("MYAGENTRAG_HOME")
                or str(Path.home() / ".myagentrag")).expanduser()
    managed_tmp = home / "tmp"
    try:
        managed_tmp.mkdir(parents=True, exist_ok=True)
        return managed_tmp
    except OSError:
        return Path(tempfile.gettempdir())


TEMP_BASE_DIR = _default_temp_base_dir()


def _sweep_stale_tmpdirs(max_age_hours=72, base=None):
    """清理异常退出遗留的 myag_* 临时目录（超过 max_age_hours 即删）；
    base 缺省用全局 TEMP_BASE_DIR，write_slices 以实际生效的根目录传入。"""
    import datetime
    root = Path(base) if base is not None else TEMP_BASE_DIR
    now = datetime.datetime.now().timestamp()
    try:
        for p in root.glob("myag_*"):
            if p.is_dir() and now - p.stat().st_mtime > max_age_hours * 3600:
                import shutil
                shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


def make_tmpdir(prefix):
    """在受管理的临时根目录下创建本次运行的工作目录"""
    import tempfile
    TEMP_BASE_DIR.mkdir(parents=True, exist_ok=True)
    _sweep_stale_tmpdirs()
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(TEMP_BASE_DIR)))


def _split_paragraphs(text):
    """优先空行分段，退化到单行"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) <= 1:
        paras = [p.strip() for p in text.splitlines() if p.strip()]
    return paras or [text]


def _hard_split(par, limit):
    """超长段落在句子边界处切（兜底任意字符）"""
    out = []
    while len(par) > limit:
        window = par[:limit]
        best = max(window.rfind("。"), window.rfind("！"), window.rfind("？"),
                   window.rfind("."), window.rfind("!"), window.rfind("?"),
                   window.rfind("\n"))
        cut = best + 1 if best > limit // 2 else limit
        out.append(par[:cut].strip())
        par = par[cut:].strip()
    if par:
        out.append(par)
    return out


def make_chunks(text, chunk_chars=CHUNK_CHARS, overlap=CHUNK_OVERLAP_CHARS):
    """纯函数：文本 -> 分片列表（段落边界对齐 + 相邻片重叠窗口），确定性输出"""
    paras = _split_paragraphs(text)
    chunks, cur, cur_len = [], [], 0
    for p in paras:
        pieces = [p] if len(p) <= chunk_chars else _hard_split(p, chunk_chars)
        for piece in pieces:
            add = len(piece) + (1 if cur else 0)
            if cur and cur_len + add > chunk_chars:
                chunks.append("\n\n".join(cur))
                tail = chunks[-1][-overlap:].strip()
                cur = [tail] if tail else []
                cur_len = len(tail)
            cur.append(piece)
            cur_len += len(piece) + 1
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def write_slices(title, source_path, content, args_slice=None, temp_base_dir=None):
    """大文档分片落盘；args_slice 非 None 时只返回该片内容。

    temp_base_dir：受管临时根目录（默认 TEMP_BASE_DIR：MYAGENTRAG_TMPDIR →
    受管 tmp → 系统临时目录兜底）。
    分片目录必须保留供 agent 按需读取（不即时清理）；过期清扫在此触发
    （P9：与 make_tmpdir 对称，纯文档路径不再依赖音视频路径顺带清理）。
    """
    temp_root = Path(temp_base_dir) if temp_base_dir else TEMP_BASE_DIR
    _sweep_stale_tmpdirs(base=temp_root)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    chunk_dir = temp_root / ("myag_slice_" + content_hash[:8])
    chunks = make_chunks(content)
    chunk_entries = []
    try:
        chunk_dir.mkdir(parents=True, exist_ok=True)
        for i, ch in enumerate(chunks, 1):
            fname = "chunk-{:03d}.md".format(i)
            (chunk_dir / fname).write_text(ch, encoding="utf-8")
            chunk_entries.append({"file": fname, "chars": len(ch),
                                  "sha256": hashlib.sha256(ch.encode("utf-8")).hexdigest()[:16]})
        manifest = {
            "platform": "slices", "title": title,
            "source_filepath": str(source_path),
            "total_chars": len(content), "chunk_chars": CHUNK_CHARS,
            "total_chunks": len(chunks), "chunk_dir": str(chunk_dir),
            "content_hash": "sha256:" + content_hash, "chunks": chunk_entries,
        }
        (chunk_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return {"platform": "slices", "title": title, "success": False,
                "error": "分片写入失败: " + str(e)}
    if args_slice is not None:
        if not (1 <= args_slice <= len(chunks)):
            return {"platform": "slices", "title": title, "success": False,
                    "error": "片号超出范围 1..%d" % len(chunks)}
        return {"platform": "slices", "title": title, "chunk": args_slice,
                "total_chunks": len(chunks), "chars": len(chunks[args_slice - 1]),
                "content": chunks[args_slice - 1], "success": True}
    return manifest