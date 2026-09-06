# -*- coding: utf-8 -*-
"""test_workspace — 知识库全生命周期（离线）"""
import json

import pytest

import workspace as ws


SRT = """1
00:00:01,000 --> 00:00:03,500
知识库检索支持全文匹配

2
00:00:04,000 --> 00:00:06,000
并且可以定位到媒体时间戳
"""


# ---------- 环境与命名 ----------

def test_ensure_fts_env_ok():
    r = ws.ensure_fts_env()
    assert r["status"] in ("ok", "switch")  # 测试宿主至少有一个可用解释器


def test_validate_name():
    assert ws.validate_name("我的库-1_")
    assert not ws.validate_name("a/b")
    assert not ws.validate_name("")
    assert not ws.validate_name("a b")


def test_auto_name_yyyymmdd(ws_mod):
    name = ws_mod._auto_ws_name(ws_mod.ws_root())
    assert len(name) == 8 and name.isdigit()
    (ws_mod.ws_root() / name).mkdir(parents=True)
    name2 = ws_mod._auto_ws_name(ws_mod.ws_root())
    assert name2 == f"{name}-2"


# ---------- 摄入 ----------

def test_ingest_text_and_files(ws_mod, tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("# 标题\n\n知识库使用 SQLite FTS5 全文检索引擎。\n\n附录：BM25 排序说明。", encoding="utf-8")
    r = ws_mod.ws_ingest("库A", content=src.read_text(encoding="utf-8"), title="研报",
                         source_type="text", source_ref=str(src), source_file=str(src))
    assert r["success"] and r["chunk_count"] >= 1
    d = ws_mod.ws_dir("库A")
    eid = r["entry_id"]
    assert (d / "entries" / eid / "full.md").exists()
    assert (d / "entries" / eid / "meta.json").exists()
    assert (d / "source" / eid / "doc.md").exists()
    meta = json.loads((d / "entries" / eid / "meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "研报" and meta["chunk_count"] == r["chunk_count"]
    con = ws_mod._connect(d / "workspace.db")
    n_fts = con.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0]
    n_chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    pass  # 连接池复用，不关闭
    assert n_fts == n_chunks == r["chunk_count"]


def test_ingest_idempotent_no_stale_fts(ws_mod, tmp_path):
    content = "知识库检索引擎设计文档，第一版内容。" * 40
    r1 = ws_mod.ws_ingest("库B", content=content, title="v1")
    # 同内容重灌（如补跑元数据）→ 同 id 更新，不产生重复条目/悬空索引
    r2 = ws_mod.ws_ingest("库B", content=content, title="v2")
    assert r2["updated"] is True and r2["entry_id"] == r1["entry_id"]
    # 内容变化 → 新条目（entry_id = sha256(全文)[:16]）
    r3 = ws_mod.ws_ingest("库B", content=content + " 追加修订。", title="v3")
    assert r3["updated"] is False and r3["entry_id"] != r1["entry_id"]
    d = ws_mod.ws_dir("库B")
    con = ws_mod._connect(d / "workspace.db")
    fts, ch = [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for t in ("entries_fts", "chunks")]
    pass  # 连接池复用，不关闭
    assert fts == ch
    v = ws_mod.ws_verify("库B")
    assert v["ok"], v["issues"]
    # 修订版可被检索到
    s = ws_mod.ws_search("库B", "追加修订")
    assert s["total"] >= 1


def test_ingest_empty(ws_mod):
    r = ws_mod.ws_ingest("库C", content="   ")
    assert not r["success"] and "error_i18n" in r


def test_ingest_invalid_name(ws_mod):
    r = ws_mod.ws_ingest("bad/name", content="内容")
    assert not r["success"] and "名称" in r["error"]


def test_srt_ingest_timestamps(ws_mod):
    r = ws_mod.ws_ingest("库D", srt_text=SRT, title="讲座", source_type="audio")
    assert r["success"]
    eid = r["entry_id"]
    d = ws_mod.ws_dir("库D")
    assert (d / "entries" / eid / "transcript.json").exists()
    con = ws_mod._connect(d / "workspace.db")
    rows = con.execute("SELECT chunk_no, start_ms, end_ms FROM chunks WHERE entry_id=?"
                       " ORDER BY chunk_no", (eid,)).fetchall()
    pass  # 连接池复用，不关闭
    assert rows and rows[0][1] == 1000 and rows[-1][2] == 6000
    t = json.loads((d / "entries" / eid / "transcript.json").read_text(encoding="utf-8"))
    assert t["segments"][0]["start_ms"] == 1000 and "char_start" in t["segments"][0]


def test_segments_ingest_bilibili_style(ws_mod):
    segs = [{"start_ms": 500, "end_ms": 2500, "text": "第一句"},
            {"start_ms": 3000, "end_ms": 5000, "text": "第二句"}]
    r = ws_mod.ws_ingest("库E", segments=segs, title="B站", source_type="bilibili",
                         raw_subtitle='[{"from":0.5}]', raw_subtitle_ext="json")
    assert r["success"]
    d = ws_mod.ws_dir("库E")
    assert (d / "source" / r["entry_id"]).exists()
    srcs = list((d / "source" / r["entry_id"]).iterdir())
    assert any(f.suffix == ".json" for f in srcs)


def test_no_keep_source(ws_mod, tmp_path):
    src = tmp_path / "x.md"
    src.write_text("内容正文，用于验证 --no-keep-source。", encoding="utf-8")
    r = ws_mod.ws_ingest("库F", content=src.read_text(encoding="utf-8"),
                         source_file=str(src), keep_source=False)
    assert r["success"]
    d = ws_mod.ws_dir("库F")
    assert not (d / "source" / r["entry_id"]).exists() or not list((d / "source" / r["entry_id"]).iterdir())


# ---------- 分片与查询构造 ----------

def test_chunks_with_offsets_slice_fidelity():
    text = "\n\n".join(f"第{i}段：" + "字" * 900 for i in range(60))
    spans = ws.chunks_with_offsets(text)
    assert len(spans) > 1
    prev_end = None
    for (s, e, ctext) in spans:
        assert text[s:e] == ctext  # 连续切片：恒为精确子串
        if prev_end is not None:
            assert s <= prev_end  # 相邻片重叠
        prev_end = e


def test_parse_srt_and_vtt():
    segs = ws.parse_srt(SRT)
    assert segs[0] == {"start_ms": 1000, "end_ms": 3500, "text": "知识库检索支持全文匹配"}
    vtt = "WEBVTT\n\n00:00.000 --> 00:02.000\nHello\n\n00:02.000 --> 00:04.000\n<b>World</b>\n"
    segs2 = ws.parse_vtt(vtt)
    assert len(segs2) == 2 and segs2[1]["text"] == "World"
    assert segs2[0]["start_ms"] == 0


def test_ts_to_ms():
    assert ws._ts_to_ms("00:00:01,000") == 1000
    assert ws._ts_to_ms("01:02:03.500") == 3723500
    assert ws._ts_to_ms("bad") is None


def test_build_fts_query():
    m, like = ws.build_fts_query("知识库 全文检索")
    assert m == '"知识库" "全文检索"'
    m, like = ws.build_fts_query("中文")
    assert m is None and like == ["中文"]
    m, _ = ws.build_fts_query("abc OR def")
    assert m == '"abc" OR "def"'
    m, _ = ws.build_fts_query('引号"内')
    assert m == '"引号""内"'
    m, _ = ws.build_fts_query("SQLite*")
    assert m == '"SQLite"*'
    m, _ = ws.build_fts_query("NEAR(a b, 5)")
    assert m == 'NEAR(a b, 5)'


def test_build_fts_query_column_prefix():
    m, like = ws.build_fts_query("publisher:清华大学")
    assert m == 'publisher:"清华大学"'
    m, like = ws.build_fts_query("author:王小明")
    assert m == 'author:"王小明"'
    m, like = ws.build_fts_query("title:ab")
    assert m is None and like == ["ab"]  # 列限定 + <3 字回退正文 LIKE


# ---------- 检索 ----------

def test_search_fts_like_prefix(ws_mod, tmp_path):
    ws_mod.ws_ingest("库G", content="知识库使用 FTS5 全文检索，支持中文子串匹配与相关性排序。" * 20,
                     title="检索研究", author="王小明", publisher="清华出版社")
    s = ws_mod.ws_search("库G", "全文检索")
    assert s["success"] and s["total"] >= 1 and s["hits"][0]["snippet"]
    assert s["hits"][0]["offset"] is not None and s["hits"][0]["chars"] > 0
    s = ws_mod.ws_search("库G", "中文")  # 2 字 → LIKE 回退
    assert s["total"] >= 1 and s["hits"][0].get("match_mode") == "like-low-precision"
    s = ws_mod.ws_search("库G", "FTS*")
    assert s["total"] >= 1
    s = ws_mod.ws_search("库G", 'publisher:"清华出版社"')
    assert s["total"] >= 1
    s = ws_mod.ws_search("库G", 'author:"王小明"')
    assert s["total"] >= 1
    s = ws_mod.ws_search("库G", "不存在词xyzq", mode="fts")
    assert s["total"] == 0 and s["success"]


def test_search_empty_query(ws_mod):
    r = ws_mod.ws_search("库H", "  ")
    assert not r["success"] and "error_i18n" in r


def test_search_all_workspaces(ws_mod):
    ws_mod.ws_ingest("库1", content="跨库内容甲：知识库架构。", title="甲")
    ws_mod.ws_ingest("库2", content="跨库内容乙：知识库实现。", title="乙")
    s = ws_mod.ws_search("库1", "知识库", all_workspaces=True)
    assert s["success"] and set(s["workspaces_searched"]) >= {"库1", "库2"}
    ws_hit = {h["workspace"] for h in s["hits"]}
    assert "库1" in ws_hit and "库2" in ws_hit


# ---------- 读取 / 校验 / 管理 ----------

def _setup(ws_mod, name="库V"):
    content = "# 文档\n\n" + "正文段落。" * 100
    r = ws_mod.ws_ingest(name, content=content, title="T", source_type="text")
    return r["entry_id"]


def test_read_entry_full_and_chunk(ws_mod):
    eid = _setup(ws_mod, "库I")
    r = ws_mod.ws_read_entry("库I", eid)
    assert r["success"] and r["entry"]["title"] == "T" and "正文段落" in r["content"]
    r = ws_mod.ws_read_entry("库I", eid, chunk_no=1)
    assert r["success"] and r["chunk"]["chunk_no"] == 1 and r["content"]
    r = ws_mod.ws_read_entry("库I", eid, chunk_no=99)
    assert not r["success"]
    r = ws_mod.ws_read_entry("库I", "0" * 16)
    assert not r["success"] and "error_i18n" in r


def test_verify_detects_tampered_full_md(ws_mod, tmp_path):
    eid = _setup(ws_mod, "库J")
    assert ws_mod.ws_verify("库J")["ok"] is True
    full = ws_mod.ws_dir("库J") / "entries" / eid / "full.md"
    full.write_text("被篡改的内容", encoding="utf-8")
    v = ws_mod.ws_verify("库J")
    assert v["ok"] is False and any(i["issue"] in ("chunk_text_mismatch", "offset_out_of_range")
                                    for i in v["issues"])


def test_verify_detects_missing_fts_rows(ws_mod):
    _setup(ws_mod, "库K")
    d = ws_mod.ws_dir("库K")
    con = ws_mod._connect(d / "workspace.db")
    con.execute("INSERT INTO entries_fts(entries_fts) VALUES('delete-all')")
    con.commit()
    pass  # 连接池复用，不关闭
    v = ws_mod.ws_verify("库K")
    assert any(i["issue"] == "fts_index" for i in v["issues"]), v["issues"]
    r = ws_mod.ws_reindex("库K")
    assert r["consistent"] is True
    assert ws_mod.ws_verify("库K")["ok"] is True


def test_stats_and_lists(ws_mod):
    _setup(ws_mod, "库L")
    ws_mod.ws_ingest("库L", srt_text=SRT, title="AV", source_type="audio")
    st = ws_mod.ws_stats("库L")
    assert st["success"] and st["entries"] == 2 and st["source_types"].get("audio") == 1
    assert st["db_bytes"] > 0
    le = ws_mod.ws_list_entries("库L")
    assert le["total"] == 2 and {e["source_type"] for e in le["entries"]} == {"text", "audio"}
    lw = ws_mod.list_workspaces()
    assert any(w["name"] == "库L" and w["entries"] == 2 for w in lw["workspaces"])


def test_vacuum(ws_mod):
    _setup(ws_mod, "库M")
    r = ws_mod.ws_vacuum("库M")
    assert r["success"] and r["db_bytes"] > 0


def test_remove_entry(ws_mod):
    eid = _setup(ws_mod, "库N")
    r = ws_mod.ws_remove_entry("库N", eid)
    assert r.get("confirm_required") is True and r["will_delete"]
    r = ws_mod.ws_remove_entry("库N", eid, yes=True)
    assert r["success"]
    d = ws_mod.ws_dir("库N")
    assert not (d / "entries" / eid).exists()
    assert ws_mod.ws_verify("库N")["ok"] is True
    r = ws_mod.ws_remove_entry("库N", eid, yes=True)
    assert not r["success"]


def test_rename_and_delete_workspace(ws_mod):
    _setup(ws_mod, "库O")
    r = ws_mod.rename_workspace("库O", "库P")
    assert r["success"] and ws_mod.ws_dir("库P") is not None
    assert ws_mod.ws_dir("库O") is None
    assert not ws_mod.rename_workspace("库P", "bad/name")["success"]
    r = ws_mod.delete_workspace("库P")
    assert r.get("confirm_required") is True
    r = ws_mod.ws_stats("库Q")
    assert not r["success"]  # 不存在的库


def test_delete_workspace_yes(ws_mod):
    _setup(ws_mod, "库R")
    r = ws_mod.delete_workspace("库R", yes=True)
    assert r["success"] and ws_mod.ws_dir("库R") is None


# ---------- 回放 ----------

def test_parse_at():
    assert ws.parse_at("12:33") == 753
    assert ws.parse_at("01:02:03") == 3723
    assert ws.parse_at("753") == 753
    assert ws.parse_at("bad") is None
    assert ws.parse_at(None) is None


def test_play_no_media(ws_mod):
    r = ws_mod.ws_ingest("库S", content="纯文本文档，无媒体副本。", keep_source=False)
    eid = r["entry_id"]
    out = ws_mod.ws_play("库S", eid, "1:00")
    assert not out["success"] and "error_i18n" in out


def test_play_player_missing_structured(ws_mod, tmp_path, monkeypatch):
    # v1.3 ⑭：无播放器 → 结构化 JSON 交由 agent 处理
    media = tmp_path / "lecture.mp3"
    media.write_bytes(b"fake")
    r = ws_mod.ws_ingest("库T", srt_text=SRT, title="讲座", source_type="audio",
                         source_file=str(media))
    eid = r["entry_id"]
    monkeypatch.setattr(ws_mod, "_player_chain", lambda: ["vlc", "potplayer", "mpv"])
    monkeypatch.setattr(ws_mod, "_locate_player", lambda key: None)
    out = ws_mod.ws_play("库T", eid, "5")
    assert out["success"] is False
    assert out["play_cmd"] is None and out["candidates"] == ["vlc", "potplayer", "mpv"]
    assert out["hint_i18n"] and out["media_file"].endswith("lecture.mp3")


def test_play_vlc_command(ws_mod, tmp_path, monkeypatch):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake")
    r = ws_mod.ws_ingest("库U", srt_text=SRT, title="V", source_type="video",
                         source_file=str(media))
    eid = r["entry_id"]
    monkeypatch.setattr(ws_mod, "_player_chain", lambda: ["vlc", "default"])
    monkeypatch.setattr(ws_mod, "_locate_player", lambda key: "C:\\vlc.exe" if key == "vlc" else None)
    launched = {}
    monkeypatch.setattr(ws_mod.subprocess, "Popen", lambda cmd, **kw: launched.update(cmd=cmd))
    out = ws_mod.ws_play("库U", eid, "01:00", duration=30)
    assert out["success"] is True and out["player"] == "vlc" and out["degraded"] is False
    assert "--start-time=60" in launched["cmd"] and "--stop-time=90" in launched["cmd"]


def test_play_default_degraded(ws_mod, tmp_path, monkeypatch):
    media = tmp_path / "a.mp3"
    media.write_bytes(b"fake")
    r = ws_mod.ws_ingest("库W", srt_text=SRT, title="A", source_type="audio",
                         source_file=str(media))
    eid = r["entry_id"]
    monkeypatch.setattr(ws_mod, "_player_chain", lambda: ["default"])
    monkeypatch.setattr(ws_mod, "_locate_player", lambda key: "default")
    monkeypatch.setattr(ws_mod.subprocess, "Popen", lambda cmd, **kw: None)
    out = ws_mod.ws_play("库W", eid, "10")
    assert out["success"] is True and out["degraded"] is True and out["note"]


# ---------- v1.5 混合检索：窗口向量 + RRF 融合 ----------

def test_ingest_with_vectors_and_fused_search(ws_mod):
    r = ws_mod.ws_ingest("库H1", content="机器学习是人工智能的一个分支。" * 50, title="ML")
    assert r["success"] and r["vectors"] >= 1
    d = ws_mod.ws_dir("库H1")
    con = ws_mod._connect(d / "workspace.db")
    n = con.execute("SELECT COUNT(*) FROM vectors WHERE entry_id=?", (r["entry_id"],)).fetchone()[0]
    pass  # 连接池复用，不关闭
    assert n == r["vectors"]
    s = ws_mod.ws_search("库H1", "机器学习", mode="fused")
    assert s["success"] and s["total"] >= 1
    assert s["hits"][0]["score_source"] in ("fused", "fts", "vector")
    assert s["vector_available"] is True
    assert s["mode"] == "fused"


def test_search_vector_mode_window_offsets(ws_mod):
    ws_mod.ws_ingest("库H2", content="深度学习与神经网络研究。" * 40, title="DL")
    s = ws_mod.ws_search("库H2", "神经网络", mode="vector")
    assert s["success"] and s["total"] >= 1
    assert s["hits"][0]["score_source"] == "vector"
    assert s["hits"][0]["win_start"] is not None and s["hits"][0]["win_end"] > s["hits"][0]["win_start"]


def test_search_fts_mode_skips_vector(ws_mod):
    ws_mod.ws_ingest("库H3", content="纯关键词检索验证内容。" * 20, title="K")
    s = ws_mod.ws_search("库H3", "关键词", mode="fts")
    assert s["success"] and s["total"] >= 1
    assert s["hits"][0]["score_source"] == "fts"


def test_search_no_embed_entry_falls_back_to_fts(ws_mod):
    r = ws_mod.ws_ingest("库H4", content="不嵌入的条目内容验证。", no_embed=True)
    assert r["success"] and r["vectors"] == 0
    s = ws_mod.ws_search("库H4", "不嵌入", mode="fused")
    assert s["success"] and s["total"] >= 1  # FTS 路仍命中
    assert s["hits"][0]["score_source"] == "fts"  # 无向量命中 → 纯 FTS 排序


def test_ingest_embed_failure_structured(ws_mod, monkeypatch):
    import embeddings
    def boom(texts, model_id=None):
        raise RuntimeError("推理引擎不可用")
    monkeypatch.setattr(embeddings, "embed_texts", boom)
    r = ws_mod.ws_ingest("库H5", content="嵌入失败测试内容。")
    assert not r["success"] and "嵌入失败" in r["error"] and "error_i18n" in r


def test_vector_blob_roundtrip(ws_mod):
    import struct as _s
    r = ws_mod.ws_ingest("库H6", content="向量存储一致性验证内容。" * 20, title="V")
    d = ws_mod.ws_dir("库H6")
    con = ws_mod._connect(d / "workspace.db")
    rows = con.execute("SELECT embedding FROM vectors WHERE entry_id=?",
                       (r["entry_id"],)).fetchall()
    pass  # 连接池复用，不关闭
    assert rows
    for (b,) in rows:
        vals = _s.unpack(f"<{len(b) // 4}f", b)
        assert len(vals) == 1024  # 假嵌入维度（与 vec0 表一致）
        assert all(-2 <= x <= 2 for x in vals)


# ---------- v0.7.1：sqlite-vec (vec0) 后端 ----------

def test_vec0_backend_ingest_and_knn(ws_mod, monkeypatch):
    """§8C.13：vec0 可用 → 向量写虚表、检索走 SQL KNN（metadata 过滤）"""
    import deps
    monkeypatch.setattr(deps, "sqlite_vec_ready",
                        lambda force=False: (True, {"version": "0.1.9"}))
    pytest.importorskip("sqlite_vec")
    r = ws_mod.ws_ingest("库V1", content="vec0 后端向量检索验证。" * 30, title="V0")
    assert r["success"] and r["vectors"] >= 1
    d = ws_mod.ws_dir("库V1")
    con = ws_mod._connect(d / "workspace.db")
    n = con.execute("SELECT COUNT(*) FROM vec_items WHERE entry_id=?",
                    (r["entry_id"],)).fetchone()[0]
    legacy = con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    pass  # 连接池复用，不关闭
    assert n == r["vectors"] and legacy == 0  # 数据进 vec_items，不写 legacy 表
    s = ws_mod.ws_search("库V1", "vec0 后端检索", mode="vector")
    assert s["success"] and s["total"] >= 1
    assert s["vector_backend"] == "sqlite-vec"
    assert s["hits"][0]["score_source"] == "vector"


def test_vec0_migration_from_legacy(ws_mod, monkeypatch):
    """§8C.13：老库 vectors 表数据 → vec_items 惰性迁移（检索时触发，幂等）"""
    import deps
    monkeypatch.setattr(deps, "sqlite_vec_ready",
                        lambda force=False: (False, {"error": "t"}))
    r = ws_mod.ws_ingest("库V2", content="迁移验证内容。" * 20, title="M")
    assert r["success"]
    d = ws_mod.ws_dir("库V2")
    con = ws_mod._connect(d / "workspace.db")
    assert con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] >= 1
    pass  # 连接池复用，不关闭
    monkeypatch.setattr(deps, "sqlite_vec_ready",
                        lambda force=False: (True, {"version": "0.1.9"}))
    pytest.importorskip("sqlite_vec")
    s = ws_mod.ws_search("库V2", "迁移验证", mode="vector")
    assert s["vector_backend"] == "sqlite-vec" and s["total"] >= 1
    con = ws_mod._connect(d / "workspace.db")
    assert con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM vec_items").fetchone()[0] >= 1
    pass  # 连接池复用，不关闭


def test_numpy_fallback_when_vec0_load_fails(ws_mod, monkeypatch):
    """§8C.13：确实加载失败才回退 numpy——探测 True 但扩展加载失败"""
    import deps
    import sqlite_vec
    monkeypatch.setattr(deps, "sqlite_vec_ready",
                        lambda force=False: (True, {"version": "0.1.9"}))

    def bad_load(con):
        con.enable_load_extension(True)
        raise RuntimeError("load fail")
    monkeypatch.setattr(sqlite_vec, "load", bad_load)
    r = ws_mod.ws_ingest("库V3", content="加载失败回退验证。" * 20, title="F")
    assert r["success"] and r["vectors"] >= 1  # 回退 legacy vectors 表
    s = ws_mod.ws_search("库V3", "加载失败回退", mode="vector")
    assert s["vector_backend"] == "numpy" and s["total"] >= 1
