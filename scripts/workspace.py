# -*- coding: utf-8 -*-
"""知识库 workspace 模块 — smart-summarize v0.6.0（T6，方案 docs/kb-sqlite-fts5-design-v1.3.md）

workspace = 目录（~/.smart-summarize/workspaces/<名>/）：
  workspace.db    SQLite FTS5+trigram（entries / chunks / entries_fts 外部内容表）
  source/<id>/    原始来源文件副本（workspace 自包含，拷走即迁移）
  entries/<id>/   meta.json + full.md（全量提取文本）+ transcript.json（音视频时间戳）

检索引擎零外部依赖（Python 标准库 sqlite3），trigram 需 SQLite ≥3.34；
v1.4 起技能固定运行在专用 Python 运行时内（~/.smart-summarize/runtime/），
trigram 由其保证，与用户系统 Python 环境彻底解耦（方案 §8B）。

与 v1.3 §2.2 的实现差异（已在设计文档补记）：chunks 表冗余存储
title/author/publisher/publish_date 四列副本——FTS5 外部内容表按 rowid 从
content 表直读全部索引列，纯 chunk_text 表无法支撑 title:/publisher: 限定检索。
分片改为连续切片（chunk_text 恒为 full.md 的精确子串 text[offset:offset+chars]），
相邻片自然重叠 CHUNK_OVERLAP_CHARS 字符，--verify 据此做逐片一致性校验。
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from deps import MANAGED_HOME
from slicing import CHUNK_CHARS, CHUNK_OVERLAP_CHARS
import messages
import embeddings
import deps

WORKSPACE_DB = "workspace.db"
AUDIO_EXTS = {'.mp3', '.wav', '.aac', '.m4a', '.flac', '.ogg', '.wma'}
VIDEO_EXTS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm'}
AV_EXTS = AUDIO_EXTS | VIDEO_EXTS

# ==================== 环境检测（FTS5/trigram；v1.4 起由专用运行时保证） ====================

FTS_PROBE_CODE = ("import sqlite3;c=sqlite3.connect(':memory:');"
                  "c.execute(\"CREATE VIRTUAL TABLE p USING fts5(x, tokenize='trigram')\");"
                  "print(sqlite3.sqlite_version)")


def check_fts_env(python_cmd=None):
    """探测解释器的 FTS5+trigram 能力。python_cmd=None 探测当前解释器。
    返回 (ok, info)；info 含 python / sqlite_version / error。
    v1.4 起技能固定运行在专用运行时内（恒可用），本函数保留作安全网与测试用。"""
    if python_cmd is None:
        info = {"python": sys.executable, "sqlite_version": sqlite3.sqlite_version}
        try:
            con = sqlite3.connect(":memory:")
            try:
                con.execute("CREATE VIRTUAL TABLE probe USING fts5(x, tokenize='trigram')")
                return True, info
            finally:
                _close(con)
        except Exception as e:
            info["error"] = str(e)
            return False, info
    cmd = [str(c) for c in python_cmd]
    info = {"python": " ".join(cmd)}
    try:
        r = subprocess.run(cmd + ["-c", FTS_PROBE_CODE],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and (r.stdout or "").strip():
            info["sqlite_version"] = r.stdout.strip()
            return True, info
        info["error"] = (r.stderr or "").strip()[-200:]
    except Exception as e:
        info["error"] = str(e)
    return False, info


def ensure_fts_env(allow_install=False):
    """当前解释器 FTS5/trigram 体检（诊断用）。

    v1.4 决策 3：技能固定使用专用运行时（trigram 由其保证），用户解释器
    探测/切换机制已废除——本函数不再触发任何安装，只报告当前状态。"""
    ok, info = check_fts_env()
    return {"status": "ok" if ok else "error", **info}


# ==================== 路径与命名（§2 / §3） ====================

_NAME_RE = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff_-]+$")


def ws_root():
    configured = os.environ.get("SMART_SUMMARIZE_WORKSPACES_DIR")
    if configured:
        return Path(configured).expanduser()
    return MANAGED_HOME / "workspaces"


def validate_name(name):
    return bool(name) and bool(_NAME_RE.fullmatch(name))


def _auto_ws_name(root):
    """未命名 → yyyymmdd，同日递增 yyyymmdd-2 / -3（§3 命名规则）"""
    day = datetime.now().strftime("%Y%m%d")
    name, i = day, 2
    while (root / name).exists():
        name = f"{day}-{i}"
        i += 1
    return name


def ws_dir(name, must_exist=True):
    """workspace 目录；must_exist 且不存在时返回 None。"""
    root = ws_root()
    p = root / name
    if must_exist and not (p / WORKSPACE_DB).exists():
        return None
    return p


def _ensure_workspace(name):
    """打开（或隐式创建）workspace，返回目录 Path；名称非法抛 ValueError。"""
    if not validate_name(name):
        raise ValueError(messages.msg("ws_name_invalid", name=name))
    p = ws_root() / name
    if not (p / WORKSPACE_DB).exists():
        (p / "entries").mkdir(parents=True, exist_ok=True)
        (p / "source").mkdir(parents=True, exist_ok=True)
        con = _connect(p / WORKSPACE_DB, create=True)
        con.executescript(SCHEMA)
        con.commit()
        _close(con)
    return p


# ==================== 数据库（§2.2；WAL，7A-3） ====================

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT,
    source_ref TEXT,
    author TEXT,
    publisher TEXT,
    publish_date TEXT,
    created_at TEXT,
    updated_at TEXT,
    total_chars INTEGER,
    chunk_count INTEGER,
    full_path TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    rowid INTEGER PRIMARY KEY,
    entry_id TEXT,
    chunk_no INTEGER,
    chunk_text TEXT,
    file_offset INTEGER,
    char_count INTEGER,
    start_ms INTEGER,
    end_ms INTEGER,
    title TEXT,
    author TEXT,
    publisher TEXT,
    publish_date TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    title,
    chunk_text,
    author,
    publisher,
    publish_date,
    content='chunks',
    content_rowid='rowid',
    tokenize='trigram'
);
CREATE TABLE IF NOT EXISTS vectors (
    rowid INTEGER PRIMARY KEY,
    entry_id TEXT,
    chunk_no INTEGER,
    win_start INTEGER,
    win_end INTEGER,
    model_id TEXT,
    embedding BLOB
);
"""

# v0.8.0 §8D 结构感知入库：标题锚点 + 源位置账本（老库经 _ensure_extra_tables 幂等迁移）
_V3_SCHEMA = """
CREATE TABLE IF NOT EXISTS headings (
    rowid INTEGER PRIMARY KEY,
    entry_id TEXT,
    heading_text TEXT,
    level INTEGER,
    offset INTEGER
);
CREATE INDEX IF NOT EXISTS idx_headings_entry ON headings(entry_id, offset);
CREATE VIRTUAL TABLE IF NOT EXISTS headings_fts USING fts5(
    heading_text,
    content='headings',
    content_rowid='rowid',
    tokenize='trigram'
);
CREATE TABLE IF NOT EXISTS source_map (
    entry_id TEXT,
    offset INTEGER,
    kind TEXT,
    v1 INTEGER,
    v2 INTEGER,
    title TEXT
);
CREATE INDEX IF NOT EXISTS idx_srcmap_entry ON source_map(entry_id, offset);
"""
SCHEMA = SCHEMA + _V3_SCHEMA


def _ensure_extra_tables(con):
    """老库幂等迁移：v0.8.0 新表（headings/headings_fts/source_map）缺失时补建。"""
    con.executescript(_V3_SCHEMA)
    con.commit()


_CONN_POOL = {}          # db_path(归一化) -> connection（进程级复用；
_VEC0_LOADED = {}  # id(con) -> True（已加载 vec0；连接由池持强引用，Connection 不可 weakref/挂属性）
                         # vec0 扩展每连接仅加载一次，重复加载在 Windows 触发
                         # "error during initialization"，故同库必须复用同一连接）


def _healthy(con):
    """连接活性探测：SELECT 1 可执行即健康（关闭/损坏 → False）。"""
    try:
        con.execute("SELECT 1")
        return True
    except sqlite3.Error:
        return False


def _connect(db_path, create=True, rebuild=False):
    """进程级连接池：每库一连接（vec0 每连接仅加载一次，规避 Windows 重复加载 init 失败）。
    - rebuild=True：丢弃池中旧连接重建（错误恢复）；
    - create=False：库文件不存在时不新建（防外部删除后静默建空库的陈旧读）；
    - 池中连接健康检查失败 → 自动重建。"""
    key = os.path.normcase(str(Path(db_path).resolve()))
    exists = Path(key).exists()
    if rebuild:
        con = _CONN_POOL.pop(key, None)
        _VEC0_LOADED.pop(id(con), None)
        if con is not None:
            con.close()
    elif key in _CONN_POOL:
        con = _CONN_POOL[key]
        if not exists and not create:
            _CONN_POOL.pop(key, None)
            raise FileNotFoundError(f"workspace 数据库不存在: {key}")
        if _healthy(con):
            return con
        _CONN_POOL.pop(key, None)  # 坏连接 → 重建
    if not exists and not create:
        raise FileNotFoundError(f"workspace 数据库不存在: {key}")
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA user_version=3")  # v3：+headings/source_map（v0.8.0 §8D）
    _ensure_extra_tables(con)  # 老库幂等迁移（新连接保证一次）
    _CONN_POOL[key] = con
    try:
        _load_vec0(con)  # vec0 虚表所在库的连接必须加载扩展（v0.7.1 §8C.13）
    except Exception:
        pass
    return con


def _close(con):
    """连接入池复用（vec0 每进程仅加载一次），close 中性化；进程退出统一回收。"""
    return None


def _pop_pool(key):
    con = _CONN_POOL.pop(key, None)
    if con is not None:
        _VEC0_LOADED.pop(id(con), None)
        con.close()


def _release_db(db_path):
    """目录改名/删除前：关闭并移除该库的池中连接（Windows 下打开句柄会阻止目录操作）。"""
    _pop_pool(os.path.normcase(str(Path(db_path).resolve())))


def _db_call(db_path, fn, retries=2, base_delay=0.2):
    """执行 fn(con)：捕获退避类 OperationalError（malformed/locked/disk i/o/busy）
    → 重建连接 + 指数退避重试 retries 次（0.2s/0.4s），仍失败才抛出。
    结构类错误（no such table 等）立即抛出，不重试。"""
    import time as _time
    last = None
    for attempt in range(retries + 1):
        con = _connect(db_path, rebuild=(attempt > 0))
        try:
            return fn(con)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if not any(t in msg for t in ("malformed", "locked", "disk i/o", "busy")):
                raise
            last = e
            if attempt < retries:
                _time.sleep(base_delay * (2 ** attempt))
    raise last


def _load_vec0(con, retries=3):
    """在本连接加载 sqlite-vec 扩展并确保 vec_items 虚表存在。
    返回 True=vec0 可用；False=回退 numpy（v1.5 §8C.13：重试后仍失败才回退——
    Windows 下 DLL 进程内重复加载存在间歇性 "error during initialization"，重试可恢复）。"""
    if _VEC0_LOADED.get(id(con)):
        return True
    if not deps.sqlite_vec_ready()[0]:
        return False
    import time as _time
    last_err = None
    for attempt in range(retries):
        try:
            import sqlite_vec
            con.enable_load_extension(True)
            sqlite_vec.load(con)
            dims = deps.EMBEDDING_MODELS[embeddings.DEFAULT_MODEL]["dims"]
            con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0("
                        f"embedding float[{dims}] distance_metric=cosine, entry_id TEXT,"
                        f" chunk_no INTEGER, win_start INTEGER, win_end INTEGER, model_id TEXT)")
            _VEC0_LOADED[id(con)] = True
            return True
        except Exception as e:
            last_err = e
            _time.sleep(0.05 * (attempt + 1))
    print(f"  ⚠️ sqlite-vec 加载失败（{str(last_err)[:80]}），向量检索回退 numpy 后端",
          file=sys.stderr)
    return False


def _migrate_legacy_vectors(con):
    """vec0 可用时，把旧 vectors 表数据迁移进 vec_items（幂等；迁移后清空旧表）。"""
    if not _load_vec0(con):
        return
    try:
        n = con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        if not n:
            return
        rows = con.execute("SELECT entry_id, chunk_no, win_start, win_end, model_id, embedding"
                           " FROM vectors").fetchall()
        con.executemany("INSERT INTO vec_items(entry_id, chunk_no, win_start, win_end,"
                        " model_id, embedding) VALUES (?,?,?,?,?,?)", rows)
        con.execute("DELETE FROM vectors")
        con.commit()
        print(f"  ♻ 已迁移 {len(rows)} 条向量至 sqlite-vec 后端", file=sys.stderr)
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


# ==================== 分片（连续切片 + 偏移跟踪） ====================

_BLANK_RE = re.compile(r"\n\s*\n")


def _para_spans(text):
    """段落在原文中的 [start, end) 区间（优先空行分段，退化到单行）"""
    spans = []
    n, i = len(text), 0
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        m = _BLANK_RE.search(text, i)
        if m:
            end = m.start()
            while end > start and text[end - 1].isspace():
                end -= 1
            spans.append((start, end))
            i = m.end()
        else:
            end = n
            while end > start and text[end - 1].isspace():
                end -= 1
            spans.append((start, end))
            i = n
    return spans


def _hard_split_spans(text, s, e, limit):
    """超长段落在句子边界处切（兜底任意字符），跟踪原文偏移"""
    out = []
    seg = text[s:e]
    pos = 0
    while len(seg) - pos > limit:
        window = seg[pos:pos + limit]
        best = max(window.rfind("。"), window.rfind("！"), window.rfind("？"),
                   window.rfind("."), window.rfind("!"), window.rfind("?"),
                   window.rfind("\n"))
        cut = pos + best + 1 if best > limit // 2 else pos + limit
        out.append((s + pos, s + cut))
        pos = cut
    if len(seg) - pos > 0:
        out.append((s + pos, e))
    return out


def chunks_with_offsets(text, chunk_chars=CHUNK_CHARS, overlap=CHUNK_OVERLAP_CHARS):
    """连续切片：每片恒为 text[start:end] 精确子串，相邻片自然重叠 overlap 字符。
    返回 [(start, end, chunk_text)]。"""
    if not text:
        return []
    pieces = []
    for (s, e) in _para_spans(text):
        if e - s <= chunk_chars:
            pieces.append((s, e))
        else:
            pieces.extend(_hard_split_spans(text, s, e, chunk_chars))
    if not pieces:
        return [(0, len(text), text)] if text.strip() else []
    chunks = []
    cur_start = cur_end = None
    for (s, e) in pieces:
        add = e - s
        if cur_end is not None and (cur_end - cur_start) + add > chunk_chars:
            chunks.append((cur_start, cur_end))
            # 重叠窗口：下一片从上一片尾部回退 overlap 字符起（保持精确子串语义）
            cur_start = max(0, cur_end - overlap)
            cur_end = e
        else:
            if cur_end is None:
                cur_start = s
            cur_end = e
    if cur_end is not None:
        chunks.append((cur_start, cur_end))
    return [(s, e, text[s:e]) for (s, e) in chunks]


# ==================== 时间戳（§6.4，v1.2） ====================

def _ts_to_ms(ts):
    """'hh:mm:ss,mmm' / 'hh:mm:ss.mmm' / 'mm:ss.mmm'（WebVTT 短格式）→ 毫秒"""
    m = re.fullmatch(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})", ts.strip())
    if not m:
        return None
    h, mi, s, ms = m.groups()
    return ((int(h or 0) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(ms.ljust(3, "0")[:3])


def _ms_to_hms(ms):
    s = int(round(ms / 1000))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def parse_srt(srt_text):
    """SRT → 段数组 [{start_ms, end_ms, text}]"""
    segs = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        tidx = next((k for k, l in enumerate(lines) if "-->" in l), None)
        if tidx is None:
            continue
        a, _, b = lines[tidx].partition("-->")
        start_ms, end_ms = _ts_to_ms(a), _ts_to_ms(b)
        text = " ".join(l.strip() for l in lines[tidx + 1:] if l.strip())
        if start_ms is not None and end_ms is not None and text:
            segs.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})
    return segs


def parse_vtt(vtt_text):
    """WebVTT → 段数组（跳过 WEBVTT 头与 NOTE 块）"""
    segs = []
    for block in re.split(r"\n\s*\n", vtt_text.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines or lines[0].strip().upper().startswith(("WEBVTT", "NOTE", "STYLE")):
            continue
        tidx = next((k for k, l in enumerate(lines) if "-->" in l), None)
        if tidx is None:
            continue
        a, _, b = lines[tidx].partition("-->")
        start_ms, end_ms = _ts_to_ms(a.strip().split(" ")[0]), _ts_to_ms(b.strip().split(" ")[0])
        text = " ".join(l.strip() for l in lines[tidx + 1:]
                        if l.strip() and not l.strip().startswith(("Kind:", "Language:")))
        text = re.sub(r"<[^>]+>", "", text)
        if start_ms is not None and end_ms is not None and text:
            segs.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})
    return segs


def text_from_segments(segments):
    """段数组 → (全文, char_spans)。全文以 \\n 连接各段，char_spans 记录每段字符区间。"""
    parts, spans, pos = [], [], 0
    for seg in segments:
        t = (seg.get("text") or "").strip()
        if not t:
            continue
        if parts:
            pos += 1  # 连接换行
        start = pos
        pos = start + len(t)
        parts.append(t)
        spans.append({"start_ms": seg.get("start_ms"), "end_ms": seg.get("end_ms"),
                      "char_start": start, "char_end": pos, "text": t})
    return "\n".join(parts), spans


def _time_range_for(char_spans, cs, ce):
    """片字符区间 [cs, ce) 内命中的段的 (首段起点, 末段终点) 毫秒；无命中返回 (None, None)"""
    first = last = None
    for sp in char_spans:
        if sp["char_end"] <= cs or sp["char_start"] >= ce:
            continue
        if first is None:
            first = sp["start_ms"]
        last = sp["end_ms"]
    return first, last


# ==================== 标题锚点（v0.8.0 §8D 结构感知入库） ====================

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_HEURISTIC_PATTERNS = [
    # 裸文本启发式（无 # 标记时）：中文书籍/规章/西文书常规模式；级别推断
    (re.compile(r"^第[一二三四五六七八九十百千零〇\d]+[章篇部卷]"), lambda m: 1),
    (re.compile(r"^第[一二三四五六七八九十百千零〇\d]+[节回部分]"), lambda m: 2),
    (re.compile(r"^条款\s*\d+"), lambda m: 2),
    (re.compile(r"^Chapter\s+\d+", re.I), lambda m: 1),
    (re.compile(r"^Section\s+\d+", re.I), lambda m: 2),
    (re.compile(r"^\d+(\.\d+)+\s*\S"), lambda m: min(1 + m.group(0).count("."), 6)),
]
_HEADING_MAX_CHARS = 60          # 仅约束裸文本启发式；markdown # 标题不受限
_SENTENCE_END = ("。", "！", "？", "；", "!", "?", ";", ".")


def _norm_heading(t):
    return re.sub(r"\s+", "", t or "").lower()


def extract_headings(text, page_count=0):
    """解析 full.md 结构标题 → [(offset, level, heading_text)]，offset 升序。

    两层来源：markdown # 标记（提取层归一化产物，不限行长）；
    裸文本启发式正则（服务 txt/老条目补建，行长 ≤60 且不以句末标点结尾）。
    页眉/重复装饰自适应过滤：同文本出现在 ≥max(3, 60% 页数) 处 → 全部丢弃。
    best-effort：误判不影响入库与检索主路，--reindex 可重建。
    """
    cands, off = [], 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        m = _MD_HEADING_RE.match(stripped)
        if m:
            cands.append((off, len(m.group(1)), m.group(2).strip()))
        elif stripped.strip():
            body = stripped.strip()
            if (len(body) <= _HEADING_MAX_CHARS
                    and not body.endswith(_SENTENCE_END)):
                for pat, lvfn in _HEURISTIC_PATTERNS:
                    hm = pat.match(body)
                    if hm:
                        cands.append((off, lvfn(hm), body))
                        break
        off += len(line)
    thresh = max(3, int(page_count * 0.6)) if page_count else 3
    freq = {}
    for _, _, t in cands:
        key = _norm_heading(t)
        freq[key] = freq.get(key, 0) + 1
    out, seen_off = [], set()
    for off_, lv, t in cands:
        key = _norm_heading(t)
        if freq[key] >= thresh or off_ in seen_off:
            continue
        seen_off.add(off_)
        out.append((off_, lv, t))
    return out


def _build_anchor_rows(text, srcmap, char_spans, entry_id):
    """一个条目的锚点行：source_map（页/章/时间账本）+ headings（结构标题）。

    srcmap：提取层账本 pdf{pages,outline} / epub{chapters}；音视频时间来自
    char_spans；docx/md 的标题已归一化为 # 标记，由解析器捕获。
    outline（PDF 书签）offset = 所指页的起始偏移（页级精度）。
    """
    sm, hd = [], []
    src = srcmap or {}
    pages = src.get("pages") or []
    for off, pno in pages:
        sm.append((entry_id, off, "page", pno, None, None))
    for off, idx, ctitle in (src.get("chapters") or []):
        sm.append((entry_id, off, "chapter", idx, None, ctitle))
    for sp in (char_spans or []):
        sm.append((entry_id, sp["char_start"], "time",
                   sp.get("start_ms"), sp.get("end_ms"), None))
    fixed = []
    for lv, ttl, pno in (src.get("outline") or []):
        if pages and 1 <= pno <= len(pages):
            fixed.append((pages[pno - 1][0], lv, ttl))
    merged = list(fixed)
    for off_, lv, t in extract_headings(text, page_count=len(pages)):
        # 解析结果与 srcmap 结构标题同文本且偏移相近 → 保留 srcmap（页级锚点更准）
        if any(abs(off_ - fo) < 500 and _norm_heading(ft) == _norm_heading(t)
               for fo, _, ft in fixed):
            continue
        merged.append((off_, lv, t))
    merged.sort(key=lambda r: r[0])
    seen = set()
    for off_, lv, t in merged:
        if off_ in seen:
            continue
        seen.add(off_)
        hd.append((entry_id, t, lv, off_))
    return sm, hd


# ==================== 摄入（§5 写入路径） ====================

def _entry_title_hint(title, text, entry_id):
    if title:
        return title
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:80]
    return entry_id


def _save_source_copy(entry_source_dir, *, source_file=None, raw_subtitle=None,
                      raw_subtitle_ext=None, source_type=None, content=None):
    """按 §2.1 规则保存原始来源副本，返回副本文件名或 None"""
    try:
        entry_source_dir.mkdir(parents=True, exist_ok=True)
        if source_file:
            dest = entry_source_dir / Path(source_file).name
            shutil.copy2(source_file, dest)
            return dest.name
        if raw_subtitle:
            dest = entry_source_dir / f"subtitle.{raw_subtitle_ext or 'txt'}"
            dest.write_text(raw_subtitle, encoding="utf-8")
            return dest.name
        if source_type == "web" and content:
            dest = entry_source_dir / "web.md"
            dest.write_text(content, encoding="utf-8")
            return dest.name
    except Exception as e:
        messages.warn("source_copy_warn", err=e)
    return None


def _store_vectors(con, entry_id, vec_windows, vec_values):
    """向量入库：vec0 虚表优先，扩展不可用回退旧 vectors 表（入库/补嵌共用）"""
    import embeddings as _emb
    packed = [struct.pack(f"<{len(v)}f", *v) for v in vec_values]
    if _load_vec0(con):
        con.execute("DELETE FROM vec_items WHERE rowid IN"
                    " (SELECT rowid FROM vec_items WHERE entry_id=?)", (entry_id,))
        con.executemany(
            "INSERT INTO vec_items(entry_id, chunk_no, win_start, win_end,"
            " model_id, embedding) VALUES (?,?,?,?,?,?)",
            [(entry_id, w[0], w[1], w[2], _emb.DEFAULT_MODEL, pk)
             for w, pk in zip(vec_windows, packed)])
    else:
        con.execute("DELETE FROM vectors WHERE entry_id=?", (entry_id,))
        con.executemany(
            "INSERT INTO vectors(entry_id, chunk_no, win_start, win_end,"
            " model_id, embedding) VALUES (?,?,?,?,?,?)",
            [(entry_id, w[0], w[1], w[2], _emb.DEFAULT_MODEL, pk)
             for w, pk in zip(vec_windows, packed)])


def ws_embed(ws_name):
    """为库内零向量条目补建向量（--embed；嵌入链闸门由 CLI 层强制，S3）"""
    d = ws_dir(ws_name)
    if d is None:
        return messages.err_result("ws_not_found", name=ws_name)
    import embeddings as _emb
    con = _connect(d / WORKSPACE_DB)
    try:
        have = set()
        try:
            have |= {r[0] for r in con.execute("SELECT DISTINCT entry_id FROM vec_items")}
        except sqlite3.Error:
            pass
        have |= {r[0] for r in con.execute("SELECT DISTINCT entry_id FROM vectors")}
        targets = [r[0] for r in con.execute("SELECT id FROM entries") if r[0] not in have]
        done, total = 0, 0
        for eid in targets:
            chunks = con.execute(
                "SELECT chunk_no, file_offset, chunk_text FROM chunks"
                " WHERE entry_id=? ORDER BY chunk_no", (eid,)).fetchall()
            wins = []
            for (no, off, ctext) in chunks:
                for (ws_, we_, wt) in _emb.split_windows(ctext):
                    wins.append((no, off + ws_, off + we_, wt))
            if not wins:
                continue
            try:
                vals = _emb.embed_texts([w[3] for w in wins], model_id=_emb.DEFAULT_MODEL)
            except RuntimeError as e:
                return messages.err_result("ws_embed_fail", err=str(e))
            _store_vectors(con, eid, [(w[0], w[1], w[2]) for w in wins], vals)
            con.commit()
            done += 1
            total += len(wins)
    finally:
        _close(con)
    if done:
        pair = messages.msg_pair("ws_embed_done", n=done, wins=total)
    else:
        pair = messages.msg_pair("ws_embed_none")
    return {"success": True, "workspace": ws_name, "embedded_entries": done,
            "vectors": total, "message": pair[messages.get_lang()], "message_i18n": pair}


def ws_ingest(ws_name, *, content=None, title=None, source_type=None, source_ref=None,
              author=None, publisher=None, publish_date=None, segments=None,
              srt_text=None, source_file=None, keep_source=True,
              raw_subtitle=None, raw_subtitle_ext=None, no_embed=False, srcmap=None,
              replace=False):
    """入库：幂等（entry_id=sha256(全文)[:16]，同 id 更新）。

    文本来源三选一：content（文档/网页/字幕清洗文本）、srt_text（whisper SRT，
    解析为段数组）、segments（B站等自带时间戳的段数组）。
    srcmap：提取层源位置账本（pdf 页偏移+outline / epub 章节），纯增量维度，
    缺省 None（老调用方/音视频时间账本由 char_spans 生成）。
    """
    try:
        d = _ensure_workspace(ws_name)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    # 构造全文与时间戳段
    char_spans = []
    if srt_text:
        segments = parse_srt(srt_text)
    if segments:
        text, char_spans = text_from_segments(segments)
    else:
        text = content or ""
    if not text or not text.strip():
        return messages.err_result("ingest_empty")

    entry_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    entry_dir = d / "entries" / entry_id
    entry_dir.mkdir(parents=True, exist_ok=True)
    (entry_dir / "full.md").write_text(text, encoding="utf-8")

    source_copy = None
    if keep_source:
        source_copy = _save_source_copy(
            d / "source" / entry_id, source_file=source_file, raw_subtitle=raw_subtitle,
            raw_subtitle_ext=raw_subtitle_ext, source_type=source_type, content=content)

    # 分片（音视频条目同时写 start_ms/end_ms）
    slice_spans = chunks_with_offsets(text)
    rows = []
    for no, (cs, ce, ctext) in enumerate(slice_spans, 1):
        start_ms = end_ms = None
        if char_spans:
            start_ms, end_ms = _time_range_for(char_spans, cs, ce)
        rows.append((entry_id, no, ctext, cs, ce - cs, start_ms, end_ms))

    # 向量嵌入（v1.5 §8C：片内窗口粒度；--no-embed 跳过并在结果标注）
    vec_windows = []
    vec_values = []
    if not no_embed:
        import embeddings as _emb
        windows = []
        for (eid, no, ctext, off, cc, sms, ems) in rows:
            for (ws_, we_, wtext) in _emb.split_windows(ctext):
                windows.append((no, off + ws_, off + we_, wtext))
        if windows:
            try:
                vec_values = _emb.embed_texts([w[3] for w in windows], model_id=_emb.DEFAULT_MODEL)
            except RuntimeError as e:
                return messages.err_result("ingest_embed_fail", err=str(e))
            vec_windows = [(w[0], w[1], w[2]) for w in windows]  # (chunk_no, win_start, win_end)

    prev = None
    if source_ref:
        con0 = _connect(d / WORKSPACE_DB)
        try:
            prev = con0.execute("SELECT title, author, publisher, publish_date FROM entries"
                                " WHERE source_ref=? AND id<>?", (source_ref, entry_id)).fetchone()
        finally:
            _close(con0)
    if prev:                            # 同源重入库：未显式指定的元数据沿用旧条目（S5）
        author = author or prev[1]
        publisher = publisher or prev[2]
        publish_date = publish_date or prev[3]
    title_final = _entry_title_hint(title or (prev and prev[0]), text, entry_id)
    now = _now_iso()
    def _write(con):
        con.executescript(SCHEMA)
        existing = con.execute("SELECT created_at FROM entries WHERE id=?", (entry_id,)).fetchone()
        created_at = existing[0] if existing else now
        # 幂等更新：先按外部内容表规则删除旧分片的 FTS 索引行（须带原值），
        # 否则旧索引指向已删除的 chunk rowid，MATCH 时报 database malformed
        for (rid, t, ct, a, pub, pd) in con.execute(
                "SELECT rowid, title, chunk_text, author, publisher, publish_date"
                " FROM chunks WHERE entry_id=?", (entry_id,)).fetchall():
            con.execute(
                "INSERT INTO entries_fts(entries_fts, rowid, title, chunk_text, author,"
                " publisher, publish_date) VALUES('delete',?,?,?,?,?,?)",
                (rid, t, ct, a, pub, pd))
        con.execute("DELETE FROM chunks WHERE entry_id=?", (entry_id,))
        for (eid, no, ctext, off, cc, sms, ems) in rows:
            cur = con.execute(
                "INSERT INTO chunks(entry_id, chunk_no, chunk_text, file_offset, char_count,"
                " start_ms, end_ms, title, author, publisher, publish_date)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (eid, no, ctext, off, cc, sms, ems, title_final,
                 author, publisher, publish_date))
            con.execute(
                "INSERT INTO entries_fts(rowid, title, chunk_text, author, publisher, publish_date)"
                " VALUES (?,?,?,?,?,?)",
                (cur.lastrowid, title_final, ctext, author, publisher, publish_date))
        con.execute("DELETE FROM vectors WHERE entry_id=?", (entry_id,))
        # 锚点与源位置账本（§8D）：幂等重建本条目行；headings_fts 全量 rebuild（量小）
        con.execute("DELETE FROM headings WHERE entry_id=?", (entry_id,))
        con.execute("DELETE FROM source_map WHERE entry_id=?", (entry_id,))
        sm_rows, hd_rows = _build_anchor_rows(text, srcmap, char_spans, entry_id)
        if sm_rows:
            con.executemany(
                "INSERT INTO source_map(entry_id, offset, kind, v1, v2, title)"
                " VALUES (?,?,?,?,?,?)", sm_rows)
        if hd_rows:
            con.executemany(
                "INSERT INTO headings(entry_id, heading_text, level, offset)"
                " VALUES (?,?,?,?)", hd_rows)
            con.execute("INSERT INTO headings_fts(headings_fts) VALUES('rebuild')")
        if vec_windows:
            _store_vectors(con, entry_id, vec_windows, vec_values)
        con.execute(
            "INSERT INTO entries(id, title, source_type, source_ref, author, publisher,"
            " publish_date, created_at, updated_at, total_chars, chunk_count, full_path)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET title=excluded.title, source_type=excluded.source_type,"
            " source_ref=excluded.source_ref, author=excluded.author, publisher=excluded.publisher,"
            " publish_date=excluded.publish_date, updated_at=excluded.updated_at,"
            " total_chars=excluded.total_chars, chunk_count=excluded.chunk_count,"
            " full_path=excluded.full_path",
            (entry_id, title_final, source_type, source_ref, author, publisher, publish_date,
             created_at, now, len(text), len(rows), "full.md"))
        con.commit()


        return existing, created_at

    # 错误恢复（退避重试 2 次）：malformed/locked 类 → 重建连接重试，仍失败才报错
    existing, created_at = _db_call(d / WORKSPACE_DB, _write)

    # 同源条目提示与替换（S4）：source_ref 相同的旧条目
    superseded = []
    con = _connect(d / WORKSPACE_DB)
    try:
        if source_ref:
            superseded = [r[0] for r in con.execute(
                "SELECT id FROM entries WHERE source_ref=? AND id<>?",
                (source_ref, entry_id)).fetchall()]
    finally:
        _close(con)
    if replace:
        for oid in superseded:          # --replace 显式授权后自动清理旧条目（内部 yes=True）
            ws_remove_entry(ws_name, oid, yes=True)

    meta = {
        "id": entry_id, "title": title_final, "source_type": source_type,
        "source_ref": source_ref, "author": author, "publisher": publisher,
        "publish_date": publish_date, "created_at": created_at, "updated_at": now,
        "total_chars": len(text), "chunk_count": len(rows), "full_path": "full.md",
        "source_copy": source_copy, "has_transcript": bool(char_spans),
        "has_vectors": bool(vec_windows),
    }
    (entry_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if char_spans:
        (entry_dir / "transcript.json").write_text(
            json.dumps({"entry_id": entry_id, "segments": char_spans},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    pair = messages.msg_pair("ingest_ok", ws=ws_name, title=title_final,
                             chars=len(text), chunks=len(rows))
    return {"success": True, "workspace": ws_name, "entry_id": entry_id,
            "title": title_final, "total_chars": len(text), "chunk_count": len(rows),
            "vectors": len(vec_windows), "updated": bool(existing),
            "supersedes": superseded, "replaced": bool(replace and superseded),
            "message": pair[messages.get_lang()],
            "message_i18n": pair}


# ==================== 检索（§6 读取路径） ====================

_FTS_KEYWORDS = {"AND", "OR", "NOT"}
# FTS5 可限定的列名前缀（title:/author:/publisher:/publish_date:/chunk_text:）
_COL_PREFIX_RE = re.compile(r"^(title|author|publisher|publish_date|chunk_text):(.+)$")


def _tokenize_query(q):
    """按空白切词、双引号保留短语 → [("phrase", s) | ("word", s)]"""
    toks, i, n = [], 0, len(q)
    while i < n:
        while i < n and q[i].isspace():
            i += 1
        if i >= n:
            break
        if q[i] == '"':
            j = q.find('"', i + 1)
            if j < 0:
                toks.append(("word", q[i:]))
                break
            toks.append(("phrase", q[i + 1:j]))
            i = j + 1
        else:
            j = i
            while j < n and not q[j].isspace():
                j += 1
            word = q[i:j]
            i = j
            if word.upper().startswith("NEAR(") and not word.endswith(")"):
                # NEAR(a b, 5)：括号内含空格，吞并后续 token 直到括号闭合
                depth = word.count("(") - word.count(")")
                while depth > 0 and i < n:
                    k = i
                    while k < n and q[k].isspace():
                        k += 1
                    j2 = k
                    while j2 < n and not q[j2].isspace():
                        j2 += 1
                    part = q[k:j2]
                    word += " " + part
                    i = j2
                    depth += part.count("(") - part.count(")")
            toks.append(("word", word))
    return toks


def build_fts_query(q):
    """用户查询 → (fts_match | None, like_terms, phrases)。

    ≥3 字词 → trigram 短语（语法转义）；自然词间以 OR 连接（召回优先，排序交给
    _fts_search_one 的 coverage 重排——小语料上 FTS5 bm25 被 IDF 钳制无区分度）；
    AND/OR/NOT、NEAR(...)、前缀 * 原样透传（显式运算符旁不做自动 OR）；
    <3 字词（trigram 无法命中）→ 回退 chunks 表 LIKE。
    phrases：参与 coverage 重排的正文自然词（去重；列前缀仅 chunk_text: 计入，
    title:/author: 等元数据过滤词不计——命中不在正文，coverage 无从统计）。
    """
    parts, like_terms, phrases = [], [], []
    _seen_phr = set()

    def _term(part, body=None):
        parts.append(part)
        if body:
            key = body.lower()
            if key not in _seen_phr:
                _seen_phr.add(key)
                phrases.append(body)

    for kind, tok in _tokenize_query(q):
        if kind == "phrase":
            _term('"' + tok.replace('"', '""') + '"', tok)
            continue
        up = tok.upper()
        if up in _FTS_KEYWORDS:
            parts.append(up)
            continue
        if up.startswith("NEAR("):
            parts.append(tok)
            continue
        cm = _COL_PREFIX_RE.match(tok)
        if cm:
            col, rest = cm.group(1), cm.group(2).strip('"')
            if len(rest) >= 3:
                quoted = f'{col}:"{rest.replace(chr(34), chr(34) * 2)}"'
                _term(quoted, rest if col == "chunk_text" else None)
            elif rest:
                like_terms.append(rest)
            continue
        if tok.endswith("*") and len(tok) > 1:
            _term('"' + tok[:-1].replace('"', '""') + '"*', tok[:-1])
            continue
        word = tok.strip('"')
        if len(word) >= 3:
            _term('"' + word.replace('"', '""') + '"', word)
        elif word:
            like_terms.append(word)
    # 自然词间自动插 OR；运算符/NEAR 旁保持用户语义（空格连接）
    match_parts, prev_natural = [], False
    for p in parts:
        natural = not (p in ("AND", "OR", "NOT") or p.upper().startswith("NEAR("))
        if match_parts and natural and prev_natural:
            match_parts.append("OR")
        match_parts.append(p)
        prev_natural = natural
    return (" ".join(match_parts) if match_parts else None), like_terms, phrases


def _like_snippet(text, terms, width=32):
    """LIKE 命中的手工摘要：首个命中词前后取窗口，『』标注"""
    low = text.lower()
    pos = -1
    for t in terms:
        p = low.find(t.lower())
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    if pos < 0:
        return text[:width * 2]
    a = max(0, pos - width)
    b = min(len(text), pos + width)
    head = "…" if a > 0 else ""
    tail = "…" if b < len(text) else ""
    return f"{head}{text[a:pos]}『{text[pos:pos + len(terms[0])]}』{text[pos + len(terms[0]):b]}{tail}"


def _row_to_hit(con, ws_name, rowid, score, snip, low_precision=False):
    c = con.execute("SELECT entry_id, chunk_no, file_offset, char_count, start_ms, end_ms"
                    " FROM chunks WHERE rowid=?", (rowid,)).fetchone()
    if not c:
        return None
    e = con.execute("SELECT id, title, author, publish_date, source_type, full_path"
                    " FROM entries WHERE id=?", (c[0],)).fetchone()
    if not e:
        return None
    media_file = None
    if e[4] in ("audio", "video"):
        sdir = ws_dir(ws_name) / "source" / c[0]
        if sdir.exists():
            for f in sorted(sdir.iterdir()):
                if f.suffix.lower() in AV_EXTS:
                    media_file = str(f)
                    break
    hit = {
        "workspace": ws_name, "entry_id": e[0], "title": e[1], "author": e[2],
        "publish_date": e[3], "source_type": e[4], "full_path": e[5],
        "chunk_no": c[1], "offset": c[2], "chars": c[3],
        "start_ms": c[4], "end_ms": c[5],
        "score": round(score, 4) if score is not None else None,
        "snippet": snip, "media_file": media_file,
    }
    if low_precision:
        hit["match_mode"] = "like-low-precision"
    return hit


def _coverage_presort(con, rows, phrases):
    """SQL 侧统计候选 chunk 命中的查询词数（coverage），不搬运正文。

    FTS5 bm25 在 chunk 级小语料上被 IDF 钳制（常见词 ≈1e-6，ORDER BY 退化
    为 rowid 序），高 coverage 的 chunk 可能落在 bm25 粗排的任意位置——
    coverage 必须对全部候选（cap 500）计算，不能只看 bm25 前若干名。
    """
    ids = [r[0] for r in rows]
    cov_expr = " + ".join(
        "(CASE WHEN chunk_text LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END)"
        for _ in phrases)
    pats = ["%" + p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            for p in phrases]
    q = ("SELECT rowid, %s FROM chunks WHERE rowid IN (%s)"
         % (cov_expr, ",".join("?" * len(ids))))
    cov = dict(con.execute(q, pats + ids).fetchall())
    return sorted(rows, key=lambda r: (-cov.get(r[0], 0), r[1]))


def _fts_search_one(con, ws_name, match, like_terms, limit, phrases=None):
    """FTS5 + LIKE 路。自然词查询两阶段：OR 召回（cap 500）→ coverage/tf 重排
    （coverage↓ > tf↓ > bm25↑）；纯运算符/列过滤查询走 bm25 原路。
    score = coverage（命中查询词数/总词数，0..1），bm25 原值全精度在 fts_detail。
    """
    phrases = phrases or []
    hits, seen = [], set()
    if match:
        rows = con.execute(
            "SELECT rowid, bm25(entries_fts) FROM entries_fts"
            " WHERE entries_fts MATCH ? ORDER BY bm25(entries_fts) LIMIT 500",
            (match,)).fetchall()
        if phrases:
            cand = _coverage_presort(con, rows, phrases)[:max(60, 3 * limit)]
            texts = dict((r[0], (r[1], r[2])) for r in con.execute(
                "SELECT rowid, chunk_text, file_offset FROM chunks WHERE rowid IN (%s)"
                % ",".join("?" * len(cand)), [r[0] for r in cand]).fetchall())
            scored = []
            for rowid, bm in cand:
                text, foff = texts.get(rowid, ("", 0))
                low = text.lower()
                cov = tf = 0
                pos = -1
                for ph in phrases:
                    p = low.find(ph.lower())
                    if p >= 0:                     # 首个命中词的分片内位置（与 snippet 高亮一致）
                        cov += 1
                        tf += min(low.count(ph.lower()), 5)   # 单词频 cap 5：防高频词刷分
                        if pos < 0 or p < pos:
                            pos = p
                scored.append((rowid, bm, cov, tf, foff + pos if pos >= 0 else None))
            scored.sort(key=lambda x: (-x[2], -x[3], x[1]))
            final = scored[:limit]
        else:
            final = [(r[0], r[1], None, None, None) for r in rows[:limit]]
        snips = {}
        if final:
            ids = [f[0] for f in final]
            snips = dict(con.execute(
                "SELECT rowid, snippet(entries_fts, 1, '『', '』', '…', 16)"
                " FROM entries_fts WHERE entries_fts MATCH ? AND rowid IN (%s)"
                % ",".join("?" * len(ids)), [match] + ids).fetchall())
        for rowid, bm, cov, tf, hoff in final:
            if phrases:
                score = round(cov / len(phrases), 4)
                kind = "coverage"
                detail = {"bm25_raw": bm, "tf": tf,
                          "coverage_terms": "%d/%d" % (cov, len(phrases))}
            else:
                score, kind, detail = None, None, {"bm25_raw": bm}
            hit = _row_to_hit(con, ws_name, rowid, score, snips.get(rowid, ""))
            if hit and (hit["entry_id"], hit["chunk_no"]) not in seen:
                seen.add((hit["entry_id"], hit["chunk_no"]))
                hit["score_source"] = "fts"
                hit["fts_detail"] = detail
                if kind:
                    hit["score_kind"] = kind
                if hoff is not None:            # 命中词真实位置（章节/页码归位依据，出口抹去）
                    hit["hit_offset"] = hoff
                hits.append(hit)
    if like_terms:
        conds = " AND ".join(["chunk_text LIKE ? ESCAPE '\\'"] * len(like_terms))
        params = ["%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                  for t in like_terms]
        for row in con.execute(
                "SELECT rowid, chunk_text, file_offset FROM chunks WHERE %s LIMIT ?" % conds,
                params + [limit]):
            low = row[1].lower()
            pos = min((low.find(t.lower()) for t in like_terms if low.find(t.lower()) >= 0),
                      default=-1)
            hit = _row_to_hit(con, ws_name, row[0], None,
                              _like_snippet(row[1], like_terms), low_precision=True)
            if pos >= 0:                        # LIKE 命中位置同样参与章节归位
                hit["hit_offset"] = row[2] + pos
            if hit and (hit["entry_id"], hit["chunk_no"]) not in seen:
                seen.add((hit["entry_id"], hit["chunk_no"]))
                hit["score_source"] = "fts"
                hits.append(hit)
    return hits


def _build_vector_hit(con, ws_name, entry_id, chunk_no, win_start, win_end, score, query):
    crow = con.execute("SELECT rowid, chunk_text, file_offset, char_count, start_ms, end_ms"
                       " FROM chunks WHERE entry_id=? AND chunk_no=?",
                       (entry_id, chunk_no)).fetchone()
    e = con.execute("SELECT id, title, author, publish_date, source_type, full_path"
                    " FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not crow or not e:
        return None
    hit = _row_to_hit(con, ws_name, crow[0], None, _like_snippet(crow[1], [query]))
    if not hit:
        return None
    hit["score"] = round(score, 4)
    hit["score_source"] = "vector"
    hit["score_kind"] = "similarity"
    hit["win_start"], hit["win_end"] = win_start, win_end
    return hit


def _vector_search_one(con, ws_name, query, limit, model_id):
    """向量路：vec0 可用 → 库内 KNN（SQL MATCH）；确实不可用 → numpy 全量扫描回退。
    返回 (hits, backend)：backend ∈ "sqlite-vec" | "numpy"。"""
    import embeddings as _emb
    backend = "numpy"
    _migrate_legacy_vectors(con)
    qv = _emb.embed_texts([query], model_id=model_id)[0]
    qblob = struct.pack(f"<{len(qv)}f", *qv)
    hits, seen = [], set()
    # 1) vec0 库内 KNN
    if _load_vec0(con):
        try:
            rows = con.execute(
                "SELECT entry_id, chunk_no, win_start, win_end, distance"
                " FROM vec_items WHERE embedding MATCH ? AND k = ? AND model_id = ?"
                " ORDER BY distance", (qblob, limit, model_id)).fetchall()
            for (entry_id, chunk_no, win_start, win_end, dist) in rows:
                if (entry_id, chunk_no) in seen:
                    continue
                hit = _build_vector_hit(con, ws_name, entry_id, chunk_no,
                                        win_start, win_end, max(0.0, 1.0 - dist), query)
                if hit:
                    seen.add((entry_id, chunk_no))
                    hits.append(hit)
            return hits, "sqlite-vec"
        except sqlite3.OperationalError:
            pass  # vec0 异常 → numpy 回退（backend 保持 numpy）
    # 2) numpy 回退：vec_items 普通扫描优先，其次旧 vectors 表
    rows = []
    try:
        rows = con.execute("SELECT entry_id, chunk_no, win_start, win_end, model_id, embedding"
                           " FROM vec_items WHERE model_id=?", (model_id,)).fetchall()
    except sqlite3.OperationalError:
        pass
    if not rows:
        try:
            rows = con.execute("SELECT entry_id, chunk_no, win_start, win_end, model_id, embedding"
                               " FROM vectors WHERE model_id=?", (model_id,)).fetchall()
        except sqlite3.OperationalError:
            return []
    vecs = [struct.unpack(f"<{len(b) // 4}f", b) for (_, _, _, _, _, b) in rows]
    top = _emb.knn_top(qv, vecs, k=limit * 4)
    for idx, score in top:
        entry_id, chunk_no, win_start, win_end = rows[idx][0], rows[idx][1], rows[idx][2], rows[idx][3]
        if (entry_id, chunk_no) in seen:
            continue
        hit = _build_vector_hit(con, ws_name, entry_id, chunk_no, win_start, win_end, score, query)
        if hit:
            seen.add((entry_id, chunk_no))
            hits.append(hit)
    return hits, backend


def _headings_search_one(con, ws_name, match, limit, phrases=None):
    """标题路（§8D 第三路）：headings_fts（trigram）检索结构标题。

    标题短文本直接 Python coverage 计分（score=coverage/总词数）；老库迁移前
    无该虚表时静默跳过（返回空，不阻塞主路）。
    """
    if not match:
        return []
    try:
        rows = con.execute(
            "SELECT rowid, bm25(headings_fts) FROM headings_fts"
            " WHERE headings_fts MATCH ? ORDER BY bm25(headings_fts) LIMIT 200",
            (match,)).fetchall()
    except sqlite3.OperationalError:
        return []
    phrases = phrases or []
    out = []
    for rowid, bm in rows[:max(limit * 3, 30)]:
        h = con.execute("SELECT entry_id, heading_text, level, offset FROM headings"
                        " WHERE rowid=?", (rowid,)).fetchone()
        if not h:
            continue
        cov = sum(1 for p in phrases if p.lower() in h[1].lower()) if phrases else None
        e = con.execute("SELECT title, source_type FROM entries WHERE id=?", (h[0],)).fetchone()
        out.append({"workspace": ws_name, "entry_id": h[0],
                    "title": e[0] if e else None, "source_type": e[1] if e else None,
                    "chunk_no": None, "chars": None,
                    "snippet": h[1], "score": round(cov / len(phrases), 4) if phrases else None,
                    "score_source": "heading",
                    "score_kind": "coverage" if phrases else None, "heading_rowid": rowid,
                    "heading_text": h[1], "heading_level": h[2], "heading_offset": h[3]})
    return out


def _rrf_fuse(fts_hits, vec_hits, limit, k=60, hd_hits=None):
    """RRF 融合（三路）：score = Σ 1/(k + rank)；同键多路命中标记并列来源。"""
    scores, hits, route_scores = {}, {}, {}
    for rank, h in enumerate(fts_hits):
        key = (h["workspace"], h["entry_id"], h["chunk_no"])
        h["score_source"] = "fts"
        hits.setdefault(key, h)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        route_scores.setdefault(key, {})["fts"] = scores[key]
    for rank, h in enumerate(vec_hits):
        key = (h["workspace"], h["entry_id"], h["chunk_no"])
        if key in hits:
            hits[key]["score_source"] = "fused"
            hits[key]["win_start"] = h.get("win_start")
            hits[key]["win_end"] = h.get("win_end")
        else:
            h["score_source"] = "vector"
            hits[key] = h
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        route_scores.setdefault(key, {})["vector"] = scores[key]
    for rank, h in enumerate(hd_hits or []):
        key = (h["workspace"], h["entry_id"], "h%d" % h["heading_rowid"])
        h["score_source"] = "heading"
        hits.setdefault(key, h)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        route_scores.setdefault(key, {})["heading"] = scores[key]
    ordered = sorted(scores.items(), key=lambda x: -x[1])[:limit]
    out = []
    for key, sc in ordered:
        h = dict(hits[key])
        h["score"] = round(sc, 4)
        h["score_kind"] = "rrf"          # RRF 排序分：跨查询不可比，不表达语义相关度
        h["scores"] = {r: round(v, 4) for r, v in route_scores[key].items()}  # 分路贡献（S6）
        out.append(h)
    return out


# ==================== 出口层整形（§8D：出处锚定源文件，full.md 内部化） ====================

def _has_column_filter(query):
    """查询含列限定语法（title:/author:/publisher:/publish_date:）→ 向量/标题路语义
    无法承担元数据过滤，检索自动降级 FTS-only（§D1/S8）"""
    return any(_COL_PREFIX_RE.match(t) for _, t in _tokenize_query(query))


def _hit_offset(h):
    # hit_offset：FTS/LIKE 命中词在分片内的真实位置；win_start：向量窗口；其余退回分片起点
    off = h.get("hit_offset")
    if off is None:
        off = h.get("win_start")
    if off is None:
        off = h.get("offset")
    if off is None:
        off = h.get("heading_offset")
    return off


def _vector_counts(con):
    """entry_id -> 向量行数（vec0 主表优先，旧 vectors 表兜底合并；S1 诊断字段）"""
    out = {}
    try:
        for eid, n in con.execute("SELECT entry_id, COUNT(*) FROM vec_items GROUP BY entry_id"):
            out[eid] = out.get(eid, 0) + n
    except sqlite3.Error:
        pass
    for eid, n in con.execute("SELECT entry_id, COUNT(*) FROM vectors GROUP BY entry_id"):
        out[eid] = out.get(eid, 0) + n
    return out


def _load_exit_info(con, d, entry_id):
    """出口层缓存：条目的标题锚点/源位置账本/元数据（聚合与整形共用，每次检索每条目一次）"""
    headings = con.execute(
        "SELECT rowid, heading_text, level, offset FROM headings"
        " WHERE entry_id=? ORDER BY offset, rowid", (entry_id,)).fetchall()
    srcmap_rows = con.execute(
        "SELECT offset, kind, v1, v2, title FROM source_map"
        " WHERE entry_id=? ORDER BY offset, rowid", (entry_id,)).fetchall()
    e = con.execute("SELECT source_type, source_ref, total_chars FROM entries"
                    " WHERE id=?", (entry_id,)).fetchone()
    full_text = None
    if not srcmap_rows:
        # 无账本条目（老库/txt）：行号出口时现算，需 full.md 全文（仅此场景加载）
        f = d / "entries" / entry_id / "full.md"
        if f.exists():
            try:
                full_text = f.read_text(encoding="utf-8")
            except Exception:
                full_text = None
    return {"headings": headings, "srcmap": srcmap_rows,
            "source_type": e[0] if e else None, "source_ref": e[1] if e else None,
            "total_chars": e[2] if e else 0, "full_text": full_text}


def _section_index(info, offset):
    """bisect：offset 所属标题在 headings 中的下标；无标题/无 offset → -1"""
    import bisect
    hs = info["headings"]
    if not hs or offset is None:
        return -1
    i = bisect.bisect_right([r[3] for r in hs], offset) - 1
    return i


def _source_loc_for(info, offset):
    """offset → 源文档位置（页/章节/时间戳；无账本文本条目给行号；再无则 None）"""
    rows = info["srcmap"]
    if rows and offset is not None:
        import bisect
        i = bisect.bisect_right([r[0] for r in rows], offset) - 1
        if i >= 0:
            _, kind, v1, v2, title = rows[i]
            if kind == "page":
                return {"kind": "pdf", "page": v1}
            if kind == "chapter":
                return {"kind": "epub", "chapter": v1, "title": title}
            if kind == "time":
                return {"kind": "time", "start_ms": v1, "end_ms": v2}
    ft = info.get("full_text")
    if ft and offset is not None and 0 <= offset <= len(ft):
        return {"kind": "line", "n": ft.count("\n", 0, offset) + 1}
    return None


def _polish_one(h, info, idx):
    """单 hit 出口整形：附 heading/section/source_loc/source_ref，抹去 full.md 内部坐标"""
    hs = info["headings"]
    if idx >= 0:
        rowid, text_, level, off_ = hs[idx]
        h["heading"] = {"text": text_, "level": level}
        h["section_ref"] = "%s#s%d" % (h["entry_id"], rowid)
        end = hs[idx + 1][3] if idx + 1 < len(hs) else (info["total_chars"] or 0)
        h["section_chars"] = max(0, end - off_)
    else:
        # 首个标题之前的区域（前言/目录）：哨兵引用 #front 保证任何命中都可精读（S2）
        h["section_ref"] = "%s#front" % h["entry_id"]
        h["section_chars"] = hs[0][3] if hs else (info["total_chars"] or 0)
    h["source_loc"] = _source_loc_for(info, _hit_offset(h))
    h["source_ref"] = info.get("source_ref")
    for k in ("offset", "win_start", "win_end", "heading_rowid",
              "heading_offset", "hit_offset", "full_path"):
        h.pop(k, None)


def _aggregate_and_polish(hits):
    """§8D 出口主过程：同 (workspace, entry, heading) 命中合并——最高分成员为代表
    （best_window 语义：snippet 随代表走），score_source 并列标注（如 fused+heading），
    附 same_section_hits 计数；随后逐 hit 整形。"""
    cache, groups, order = {}, {}, []
    for gi, h in enumerate(hits):
        ck = (h.get("workspace"), h.get("entry_id"))
        info = cache.get(ck)
        if info is None:
            try:
                d = ws_dir(h["workspace"])
                con = _connect(d / WORKSPACE_DB)
                info = _load_exit_info(con, d, h["entry_id"])
            except Exception:
                info = {"headings": [], "srcmap": [], "source_type": None,
                        "source_ref": None, "total_chars": 0, "full_text": None}
            cache[ck] = info
        idx = _section_index(info, _hit_offset(h))
        # 无标题锚点 → 不聚合（保持 chunk/窗口既有粒度）；有锚点才按章节合并
        key = ((ck[0], ck[1], "h%d" % info["headings"][idx][0]) if idx >= 0
               else ("raw", gi))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((h, idx, info))
    out = []
    for key in order:
        members = groups[key]
        rep = dict(members[0][0])
        idx, info = members[0][1], members[0][2]
        _rs = {}
        for _m, _i, _f in members:
            for _r, _v in (_m.get("scores") or {}).items():
                _rs[_r] = max(_rs.get(_r, 0.0), _v)
        if _rs:
            rep["scores"] = {r: round(v, 4) for r, v in _rs.items()}
        labels = [lbl for lbl in ("fused", "heading", "fts", "vector")
                  if any(m[0].get("score_source") == lbl for m in members)]
        if labels:
            rep["score_source"] = "+".join(labels)
        if len(members) > 1:
            rep["same_section_hits"] = len(members) - 1
        _polish_one(rep, info, idx)
        out.append(rep)
    return out


def ws_search(ws_name, query, limit=20, all_workspaces=False, mode="fused",
              no_embed=False):
    """混合检索（v1.5 §8C）：FTS5 路 + 向量路 RRF 融合；mode=fused|fts|vector。

    向量路不可用（组件缺失/推理失败）时：mode=fused 降级为 FTS-only 并在结果标注
    vector_available=False；mode=vector 则返回结构化错误（不静默降级）。
    """
    match, like_terms, phrases = build_fts_query(query)
    col_filter = _has_column_filter(query)   # 列限定 → 向量/标题路不参与（元数据过滤语义）
    if mode in ("fts", "fused") and not match and not like_terms:
        return messages.err_result("ws_search_empty")
    names = ([p.name for p in ws_root().iterdir() if (p / WORKSPACE_DB).exists()]
             if all_workspaces else [ws_name])
    if mode == "vector" and not ws_dir(ws_name) and not all_workspaces:
        return messages.err_result("ws_not_found", name=ws_name)
    fts_all, hd_all, vec_all, searched = [], [], [], []
    degraded = {"vector_available": mode in ("vector", "fused")}
    vector_available = mode in ("vector", "fused") and not col_filter
    vector_backend = None
    skip_vector = bool(os.environ.get("SMART_SUMMARIZE_NO_RUNTIME"))  # 测试模式：全传统路径
    if skip_vector:
        degraded["vector_available"] = False
    for ws in names:
        d = ws_dir(ws)
        if d is None:
            continue
        searched.append(ws)

        def _run(con, _ws=ws):
            f_hits = (_fts_search_one(con, _ws, match, like_terms, limit,
                                      phrases=phrases)
                      if mode in ("fts", "fused") else [])
            h_hits = (_headings_search_one(con, _ws, match, limit, phrases=phrases)
                      if mode in ("fts", "fused") and not col_filter else [])
            v_hits, backend = [], None
            if mode in ("vector", "fused") and not skip_vector and not col_filter:
                try:
                    v_hits, backend = _vector_search_one(con, _ws, query, limit,
                                                         embeddings.DEFAULT_MODEL)
                except RuntimeError:
                    if mode == "vector":
                        raise
                    degraded["vector_available"] = False
            return f_hits, h_hits, v_hits, backend

        f_hits, h_hits, v_hits, backend = _db_call(d / WORKSPACE_DB, _run)
        fts_all.extend(f_hits)
        hd_all.extend(h_hits)
        vec_all.extend(v_hits)
        if backend:
            vector_backend = backend
    if mode == "fts":
        total_hits = (fts_all + hd_all)[:limit]
    elif mode == "vector":
        total_hits = sorted(vec_all, key=lambda h: -h["score"])[:limit]
    else:
        total_hits = _rrf_fuse(fts_all, vec_all, limit, hd_hits=hd_all)
    total_hits = _aggregate_and_polish(total_hits)
    result = {"success": True, "workspace": None if all_workspaces else ws_name,
            "workspaces_searched": searched, "query": query,
            "mode": ("fts" if col_filter else mode),
            "column_filter": col_filter,
            "keyword_miss": bool(mode == "fused" and not col_filter
                                 and not fts_all and vec_all and not skip_vector),
            "fts_query": match, "like_fallback_terms": like_terms,
            "vector_available": vector_available and degraded["vector_available"],
            "vector_backend": vector_backend,
            "total": len(total_hits), "hits": total_hits}
    return result


# ==================== 条目读取 / 删除（§4） ====================

def _get_entry(con, entry_id):
    return con.execute(
        "SELECT id, title, source_type, source_ref, author, publisher, publish_date,"
        " created_at, updated_at, total_chars, chunk_count, full_path"
        " FROM entries WHERE id=?", (entry_id,)).fetchone()


def _entry_meta(e):
    """entries 行 → agent 可见元数据（不含 full.md 内部路径）"""
    return {"id": e[0], "title": e[1], "source_type": e[2], "source_ref": e[3],
            "author": e[4], "publisher": e[5], "publish_date": e[6],
            "created_at": e[7], "updated_at": e[8], "total_chars": e[9],
            "chunk_count": e[10]}


def _read_front(con, d, ws_name, entry_id):
    """#front 哨兵：首个标题之前的区域（前言/目录）精读（S2）"""
    e = _get_entry(con, entry_id)
    if not e:
        return messages.err_result("ws_entry_not_found", eid=entry_id, ws=ws_name)
    full_file = d / "entries" / entry_id / "full.md"
    text = full_file.read_text(encoding="utf-8") if full_file.exists() else ""
    row = con.execute("SELECT MIN(offset) FROM headings WHERE entry_id=?",
                      (entry_id,)).fetchone()
    end = row[0] if row and row[0] is not None else len(text)
    info = _load_exit_info(con, d, entry_id)
    return {"success": True, "workspace": ws_name, "entry": _entry_meta(e),
            "section": {"ref": "%s#front" % entry_id, "heading": None, "chars": end,
                        "source_loc": _source_loc_for(info, 0)},
            "content": text[:end]}


def _read_section(con, d, ws_name, ref):
    """按检索返回的 section_ref（<entry_id>#s<rowid>）精读整节内容。

    ref 为不透明引用：agent 原样传回即可，无需理解内部结构。
    """
    if ref.endswith("#front"):
        return _read_front(con, d, ws_name, ref[:-len("#front")])
    try:
        entry_id, srowid = ref.split("#s", 1)
        srowid = int(srowid)
    except ValueError:
        return messages.err_result("ws_section_not_found", ref=ref)
    e = _get_entry(con, entry_id)
    if not e:
        return messages.err_result("ws_entry_not_found", eid=entry_id, ws=ws_name)
    hs = con.execute("SELECT rowid, heading_text, level, offset FROM headings"
                     " WHERE entry_id=? AND rowid=?", (entry_id, srowid)).fetchone()
    if not hs:
        return messages.err_result("ws_section_not_found", ref=ref)
    nxt = con.execute("SELECT offset FROM headings WHERE entry_id=? AND offset>?"
                      " ORDER BY offset LIMIT 1", (entry_id, hs[3])).fetchone()
    full_file = d / "entries" / entry_id / "full.md"
    text = full_file.read_text(encoding="utf-8") if full_file.exists() else ""
    end = nxt[0] if nxt else len(text)
    info = _load_exit_info(con, d, entry_id)
    sec = {"ref": ref, "heading": {"text": hs[1], "level": hs[2]},
           "chars": max(0, end - hs[3]),
           "source_loc": _source_loc_for(info, hs[3])}
    return {"success": True, "workspace": ws_name, "entry": _entry_meta(e),
            "section": sec, "content": text[hs[3]:end]}


def ws_read_entry(ws_name, entry_id, chunk_no=None, section=None):
    d = ws_dir(ws_name)
    if d is None:
        return messages.err_result("ws_not_found", name=ws_name)
    con = _connect(d / WORKSPACE_DB)
    try:
        if section:
            return _read_section(con, d, ws_name, section)
        e = _get_entry(con, entry_id)
        if not e:
            return messages.err_result("ws_entry_not_found", eid=entry_id, ws=ws_name)
        meta = _entry_meta(e)
        if chunk_no is not None:
            c = con.execute("SELECT chunk_no, chunk_text, file_offset, char_count, start_ms, end_ms"
                            " FROM chunks WHERE entry_id=? AND chunk_no=?", (entry_id, chunk_no)).fetchone()
            if not c:
                return messages.err_result("ws_entry_not_found", eid=f"{entry_id}#chunk{chunk_no}", ws=ws_name)
            return {"success": True, "workspace": ws_name, "entry": meta,
                    "chunk": {"chunk_no": c[0], "chars": c[3],
                              "start_ms": c[4], "end_ms": c[5]}, "content": c[1]}
        full_file = d / "entries" / entry_id / "full.md"
        text = full_file.read_text(encoding="utf-8") if full_file.exists() else ""
        return {"success": True, "workspace": ws_name, "entry": meta, "chunk": None, "content": text}
    finally:
        _close(con)


def ws_remove_entry(ws_name, entry_id, yes=False):
    d = ws_dir(ws_name)
    if d is None:
        return messages.err_result("ws_not_found", name=ws_name)
    con = _connect(d / WORKSPACE_DB)
    try:
        e = _get_entry(con, entry_id)
        if not e:
            return messages.err_result("ws_entry_not_found", eid=entry_id, ws=ws_name)
    finally:
        _close(con)
    if not yes:
        return {"success": False, "confirm_required": True,
                "error": messages.msg("ws_confirm_required"),
                "error_i18n": messages.msg_pair("ws_confirm_required"),
                "will_delete": [str(d / "entries" / entry_id), str(d / "source" / entry_id)]}
    con = _connect(d / WORKSPACE_DB)
    try:
        con.execute("DELETE FROM chunks WHERE entry_id=?", (entry_id,))
        con.execute("DELETE FROM headings WHERE entry_id=?", (entry_id,))
        con.execute("DELETE FROM source_map WHERE entry_id=?", (entry_id,))
        con.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        con.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
        con.execute("INSERT INTO headings_fts(headings_fts) VALUES('rebuild')")
        con.commit()
    finally:
        _close(con)
    shutil.rmtree(d / "entries" / entry_id, ignore_errors=True)
    shutil.rmtree(d / "source" / entry_id, ignore_errors=True)
    pair = messages.msg_pair("ws_entry_removed", eid=entry_id)
    return {"success": True, "workspace": ws_name, "entry_id": entry_id,
            "message": pair[messages.get_lang()], "message_i18n": pair}


# ==================== workspace 管理操作（§3 / §4） ====================

def create_workspace(name):
    if not validate_name(name):
        return messages.err_result("ws_name_invalid", name=name)
    if ws_dir(name) is not None:
        return messages.err_result("ws_exists", name=name)
    d = _ensure_workspace(name)
    pair = messages.msg_pair("ws_created", name=name)
    return {"success": True, "workspace": name, "path": str(d),
            "message": pair[messages.get_lang()], "message_i18n": pair}


def list_workspaces():
    root = ws_root()
    out = []
    if root.exists():
        for p in sorted(root.iterdir()):
            if not (p / WORKSPACE_DB).exists():
                continue
            con = _connect(p / WORKSPACE_DB)
            try:
                row = con.execute("SELECT COUNT(*), COALESCE(SUM(total_chars),0),"
                                  " MAX(updated_at) FROM entries").fetchone()
            finally:
                _close(con)
            out.append({"name": p.name, "entries": row[0], "total_chars": row[1],
                        "last_updated": row[2], "path": str(p)})
    return {"success": True, "workspaces": out, "total": len(out)}


def delete_workspace(name, yes=False):
    d = ws_dir(name)
    if d is None:
        return messages.err_result("ws_not_found", name=name)
    if not yes:
        return {"success": False, "confirm_required": True,
                "error": messages.msg("ws_confirm_required"),
                "error_i18n": messages.msg_pair("ws_confirm_required"),
                "will_delete": str(d)}
    _release_db(d / WORKSPACE_DB)
    shutil.rmtree(d, ignore_errors=True)
    return {"success": True, "workspace": name, "deleted": True}


def rename_workspace(old, new):
    src = ws_dir(old)
    if src is None:
        return messages.err_result("ws_not_found", name=old)
    if not validate_name(new):
        return messages.err_result("ws_name_invalid", name=new)
    dst = ws_root() / new
    if dst.exists():
        return messages.err_result("ws_exists", name=new)
    _release_db(src / WORKSPACE_DB)
    src.rename(dst)
    pair = messages.msg_pair("ws_renamed", old=old, new=new)
    return {"success": True, "workspace": new,
            "message": pair[messages.get_lang()], "message_i18n": pair}


def ws_stats(name):
    d = ws_dir(name)
    if d is None:
        return messages.err_result("ws_not_found", name=name)
    con = _connect(d / WORKSPACE_DB)
    try:
        row = con.execute("SELECT COUNT(*), COALESCE(SUM(total_chars),0),"
                          " COALESCE(SUM(chunk_count),0) FROM entries").fetchone()
        dist = dict(con.execute("SELECT COALESCE(source_type,'?'), COUNT(*) FROM entries"
                                " GROUP BY source_type").fetchall())
    finally:
        _close(con)
    db_size = (d / WORKSPACE_DB).stat().st_size
    return {"success": True, "workspace": name, "path": str(d),
            "entries": row[0], "total_chars": row[1], "chunks": row[2],
            "vectors": sum(_vector_counts(con).values()),
            "db_bytes": db_size, "source_types": dist}


def ws_list_entries(name):
    d = ws_dir(name)
    if d is None:
        return messages.err_result("ws_not_found", name=name)
    con = _connect(d / WORKSPACE_DB)
    try:
        rows = con.execute(
            "SELECT id, title, source_type, author, publish_date, total_chars,"
            " chunk_count, updated_at FROM entries ORDER BY updated_at DESC").fetchall()
    finally:
        _close(con)
    vc = _vector_counts(con)
    entries = [{"id": r[0], "title": r[1], "source_type": r[2], "author": r[3],
                "publish_date": r[4], "total_chars": r[5], "chunk_count": r[6],
                "updated_at": r[7], "vectors": vc.get(r[0], 0)} for r in rows]
    return {"success": True, "workspace": name, "total": len(entries), "entries": entries}


def ws_verify(name):
    """完整性校验（§4）：片数一致、chunk_text 与 full.md 逐片一致、覆盖无遗漏、FTS 行数一致"""
    d = ws_dir(name)
    if d is None:
        return messages.err_result("ws_not_found", name=name)
    issues = []
    con = _connect(d / WORKSPACE_DB)
    try:
        entries = con.execute("SELECT id, chunk_count, total_chars FROM entries").fetchall()
        for (eid, meta_count, _total) in entries:
            actual = con.execute("SELECT COUNT(*) FROM chunks WHERE entry_id=?", (eid,)).fetchone()[0]
            if actual != meta_count:
                issues.append({"entry_id": eid, "issue": "chunk_count",
                               "detail": f"meta={meta_count} actual={actual}"})
        # FTS 索引与 chunks 的一致性：integrity-check(rank=1) 逐行比对索引与外部内容表。
        # 注意 COUNT(*) 在外部内容表上会被优化为直查 chunks，检不出索引缺失/悬空。
        try:
            con.execute("INSERT INTO entries_fts(entries_fts, rank) VALUES('integrity-check', 1)")
        except sqlite3.DatabaseError as e:
            issues.append({"entry_id": None, "issue": "fts_index",
                           "detail": str(e)[:120]})
        # 锚点完整性（§8D）：headings_fts 索引一致 + headings/source_map offset 界内
        try:
            con.execute("INSERT INTO headings_fts(headings_fts, rank) VALUES('integrity-check', 1)")
        except sqlite3.DatabaseError as e:
            issues.append({"entry_id": None, "issue": "headings_fts_index",
                           "detail": str(e)[:120]})
        for (eid2, hrow, off2, tot) in con.execute(
                "SELECT h.entry_id, h.rowid, h.offset, e.total_chars FROM headings h"
                " LEFT JOIN entries e ON e.id=h.entry_id").fetchall():
            if off2 is None or off2 < 0 or off2 > (tot or 0):
                issues.append({"entry_id": eid2, "issue": "heading_offset_out_of_range",
                               "detail": f"heading {hrow}: offset={off2} total={tot}"})
        for (eid3, off3) in con.execute("SELECT entry_id, offset FROM source_map").fetchall():
            if off3 is None or off3 < 0:
                issues.append({"entry_id": eid3, "issue": "srcmap_offset_invalid",
                               "detail": f"offset={off3}"})
    finally:
        _close(con)
    for (eid, meta_count, _total) in entries:
        full_file = d / "entries" / eid / "full.md"
        if not full_file.exists():
            issues.append({"entry_id": eid, "issue": "full_md_missing", "detail": str(full_file)})
            continue
        full = full_file.read_text(encoding="utf-8")
        con = _connect(d / WORKSPACE_DB)
        try:
            chunks = con.execute("SELECT chunk_no, chunk_text, file_offset, char_count"
                                 " FROM chunks WHERE entry_id=? ORDER BY file_offset", (eid,)).fetchall()
        finally:
            _close(con)
        covered_end = None
        for (no, ctext, off, cc) in chunks:
            if off < 0 or off + cc > len(full):
                issues.append({"entry_id": eid, "issue": "offset_out_of_range",
                               "detail": f"chunk {no}: offset={off} chars={cc} full={len(full)}"})
                continue
            if full[off:off + cc] != ctext:
                issues.append({"entry_id": eid, "issue": "chunk_text_mismatch", "detail": f"chunk {no}"})
            if covered_end is not None and off > covered_end and full[covered_end:off].strip():
                issues.append({"entry_id": eid, "issue": "coverage_gap",
                               "detail": f"chunk {no}: gap [{covered_end},{off})"})
            covered_end = (off + cc) if covered_end is None else max(covered_end, off + cc)
        if covered_end is not None and covered_end < len(full) and full[covered_end:].strip():
            issues.append({"entry_id": eid, "issue": "coverage_tail",
                           "detail": f"uncovered tail from {covered_end}"})
    pair = messages.msg_pair("ws_verify_ok")
    return {"success": True, "workspace": name, "entries": len(entries),
            "issues": issues, "issue_count": len(issues),
            "ok": not issues,
            "message": pair[messages.get_lang()] if not issues else
            f"{issues[0]['issue']} ... x{len(issues)}",
            "message_i18n": pair}


def ws_reindex(name):
    """重建索引：FTS 全量 rebuild + 标题锚点重解析（§8D 老条目补建；source_map 是
    提取层产物不动）。"""
    d = ws_dir(name)
    if d is None:
        return messages.err_result("ws_not_found", name=name)
    con = _connect(d / WORKSPACE_DB)
    try:
        con.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
        con.execute("DELETE FROM headings")
        rebuilt = 0
        for (eid,) in con.execute("SELECT id FROM entries").fetchall():
            f = d / "entries" / eid / "full.md"
            if not f.exists():
                continue
            text = f.read_text(encoding="utf-8")
            page_count = con.execute(
                "SELECT COUNT(*) FROM source_map WHERE entry_id=? AND kind='page'",
                (eid,)).fetchone()[0]
            rows = extract_headings(text, page_count=page_count)
            con.executemany(
                "INSERT INTO headings(entry_id, heading_text, level, offset)"
                " VALUES (?,?,?,?)",
                [(eid, t, lv, off) for (off, lv, t) in rows])
            rebuilt += len(rows)
        con.execute("INSERT INTO headings_fts(headings_fts) VALUES('rebuild')")
        con.commit()
        fcount = con.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0]
        ccount = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        hcount = con.execute("SELECT COUNT(*) FROM headings").fetchone()[0]
        vcount = sum(_vector_counts(con).values())
    finally:
        _close(con)
    pair = messages.msg_pair("ws_reindexed")
    return {"success": True, "workspace": name, "fts_rows": fcount, "chunks_rows": ccount,
            "headings_rows": hcount, "vectors_rows": vcount, "consistent": fcount == ccount,
            "message": pair[messages.get_lang()], "message_i18n": pair}


def ws_vacuum(name):
    d = ws_dir(name)
    if d is None:
        return messages.err_result("ws_not_found", name=name)
    con = _connect(d / WORKSPACE_DB)
    try:
        con.execute("VACUUM")
    finally:
        _close(con)
    pair = messages.msg_pair("ws_vacuumed")
    return {"success": True, "workspace": name, "db_bytes": (d / WORKSPACE_DB).stat().st_size,
            "message": pair[messages.get_lang()], "message_i18n": pair}


# ==================== 定位回放（§6.4 / 7A：播放器探测链按 OS 参数化） ====================

def _is_wsl():
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    proc = Path("/proc/version")
    if proc.exists():
        try:
            return "microsoft" in proc.read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            return False
    return False


_WIN_VLC_PATHS = (r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                  r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe")
_WIN_POT_PATHS = (r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
                  r"C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini64.exe")
_WSL_VLC_PATHS = ("/mnt/c/Program Files/VideoLAN/VLC/vlc.exe",
                  "/mnt/c/Program Files (x86)/VideoLAN/VLC/vlc.exe")


def _player_chain():
    if _is_wsl():
        return ["wslview", "vlc-win"]
    if os.name == "nt":
        return ["vlc", "potplayer", "mpv", "default"]
    if sys.platform == "darwin":
        return ["vlc", "iina", "mpv", "default"]
    return ["vlc", "mpv", "default"]


def _probe_candidates():
    """§6.4 结构化输出中的 candidates 字段"""
    chain = _player_chain()
    return [k for k in chain if k != "default"]


def _locate_player(key):
    if key == "wslview":
        return shutil.which("wslview")
    if key == "vlc-win":
        for p in _WSL_VLC_PATHS:
            if Path(p).exists():
                return p
        return None
    if key == "vlc":
        exe = shutil.which("vlc") or shutil.which("vlc.exe")
        if exe:
            return exe
        if os.name == "nt":
            for p in _WIN_VLC_PATHS:
                if Path(p).exists():
                    return p
        return None
    if key == "potplayer":
        exe = shutil.which("PotPlayerMini64.exe")
        if exe:
            return exe
        for p in _WIN_POT_PATHS:
            if Path(p).exists():
                return p
        return None
    if key == "iina":
        return shutil.which("iina")
    if key == "mpv":
        return shutil.which("mpv")
    if key == "default":
        return "default"
    return None


def parse_at(at):
    """'mm:ss' / 'hh:mm:ss' / 秒数 → 秒（int）；非法返回 None"""
    if at is None:
        return None
    at = str(at).strip()
    if re.fullmatch(r"\d+", at):
        return int(at)
    m = re.fullmatch(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2})", at)
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s)


def ws_play(ws_name, entry_id, at, duration=None):
    d = ws_dir(ws_name)
    if d is None:
        return messages.err_result("ws_not_found", name=ws_name)
    start_s = parse_at(at)
    if start_s is None:
        return messages.err_result("play_at_invalid", v=at)
    end_s = start_s + int(duration) if duration else None

    # 找本地媒体文件副本
    media = None
    sdir = d / "source" / entry_id
    if sdir.exists():
        for f in sorted(sdir.iterdir()):
            if f.suffix.lower() in AV_EXTS:
                media = f
                break
    if media is None:
        return messages.err_result("play_no_media")

    player_key, exe = None, None
    probed = []
    for key in _player_chain():
        found = _locate_player(key)
        probed.append(key)
        if found:
            player_key, exe = key, found
            break
    if player_key is None:
        # v1.3 ⑭：播放器缺失 → 只输出结构化 JSON 交由 agent 处理
        return {"success": False,
                "error": messages.msg("play_no_player"),
                "error_i18n": messages.msg_pair("play_no_player"),
                "candidates": _probe_candidates(),
                "hint": messages.msg("play_no_player_hint"),
                "hint_i18n": messages.msg_pair("play_no_player_hint"),
                "play_cmd": None, "media_file": str(media), "start_s": start_s}

    degraded = False
    if player_key == "vlc" or player_key == "vlc-win":
        cmd = [exe, str(media), f"--start-time={start_s}"]
        if end_s:
            cmd.append(f"--stop-time={end_s}")
    elif player_key == "potplayer":
        cmd = [exe, str(media), f"/seek={_ms_to_hms(start_s * 1000)}"]
    elif player_key == "mpv":
        cmd = [exe, str(media), f"--start={_ms_to_hms(start_s * 1000)}"]
        if end_s:
            cmd.append(f"--end={_ms_to_hms(end_s * 1000)}")
    elif player_key == "iina":
        cmd = [exe, str(media), f"--mpv-start={_ms_to_hms(start_s * 1000)}"]
    else:  # wslview / default：系统默认关联，只能从头播（§7A 修正：按 OS 参数化，不硬编码 start）
        degraded = True
        if player_key == "wslview":
            cmd = [exe, str(media)]
        elif os.name == "nt":
            cmd = ["cmd", "/c", "start", "", str(media)]
        elif sys.platform == "darwin":
            cmd = ["open", str(media)]
        else:
            cmd = ["xdg-open", str(media)]

    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return {"success": False, "error": str(e), "play_cmd": cmd, "player": player_key}
    pair = messages.msg_pair("play_launched", player=player_key)
    out = {"success": True, "workspace": ws_name, "entry_id": entry_id,
           "player": player_key, "play_cmd": cmd, "media_file": str(media),
           "start_ms": start_s * 1000, "end_ms": end_s * 1000 if end_s else None,
           "degraded": degraded,
           "message": pair[messages.get_lang()], "message_i18n": pair}
    if degraded:
        dpair = messages.msg_pair("play_degraded")
        out["note"] = dpair[messages.get_lang()]
        out["note_i18n"] = dpair
    return out
