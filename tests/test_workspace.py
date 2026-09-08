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
    m, like, ph = ws.build_fts_query("知识库 全文检索")
    assert m == '"知识库" OR "全文检索"'  # 自然词间自动 OR（召回优先）
    assert ph == ["知识库", "全文检索"]
    m, like, ph = ws.build_fts_query("中文")
    assert m is None and like == ["中文"] and ph == []
    m, _, ph = ws.build_fts_query("abc OR def")
    assert m == '"abc" OR "def"'  # 显式运算符旁不重复插 OR
    m, _, ph = ws.build_fts_query("abc AND def")
    assert m == '"abc" AND "def"'  # 显式 AND 语义保留
    m, _, ph = ws.build_fts_query('引号"内')
    assert m == '"引号""内"'
    m, _, ph = ws.build_fts_query("SQLite*")
    assert m == '"SQLite"*' and ph == ["SQLite"]
    m, _, ph = ws.build_fts_query("NEAR(a b, 5)")
    assert m == 'NEAR(a b, 5)' and ph == []


def test_build_fts_query_column_prefix():
    m, like, ph = ws.build_fts_query("publisher:清华大学")
    assert m == 'publisher:"清华大学"' and ph == []  # 元数据过滤词不进 coverage
    m, like, ph = ws.build_fts_query("author:王小明")
    assert m == 'author:"王小明"' and ph == []
    m, like, ph = ws.build_fts_query("title:ab")
    assert m is None and like == ["ab"]  # 列限定 + <3 字回退正文 LIKE
    m, like, ph = ws.build_fts_query('chunk_text:知识库')
    assert m == 'chunk_text:"知识库"' and ph == ["知识库"]  # 正文列限定计入


# ---------- 检索 ----------

def test_search_fts_like_prefix(ws_mod, tmp_path):
    ws_mod.ws_ingest("库G", content="知识库使用 FTS5 全文检索，支持中文子串匹配与相关性排序。" * 20,
                     title="检索研究", author="王小明", publisher="清华出版社")
    s = ws_mod.ws_search("库G", "全文检索")
    assert s["success"] and s["total"] >= 1 and s["hits"][0]["snippet"]
    assert "offset" not in s["hits"][0]  # §8D：full.md 内部坐标不出口
    assert s["hits"][0]["chunk_no"] is not None and s["hits"][0]["chars"] > 0
    assert "source_ref" in s["hits"][0]  # 出处锚定源文件
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


def test_search_fts_or_recall_coverage(ws_mod):
    """F1：自然多词 OR 召回 + coverage 重排，告别 -0.0"""
    ws_mod.ws_ingest("库F1a", content="本节只讨论 reserve 机制的应用。" * 5, title="A")
    ws_mod.ws_ingest("库F1a", content="reserve 与重新分配的差异在此讨论，先讲 reserve。" * 5, title="B")
    s = ws_mod.ws_search("库F1a", "reserve 重新分配", mode="fts")
    assert s["success"] and s["fts_query"] == '"reserve" OR "重新分配"'
    hits = s["hits"]
    assert len(hits) == 2
    by_title = {h["title"]: h for h in hits}
    assert by_title["B"]["score"] == 1.0 and by_title["A"]["score"] == 0.5
    assert hits[0]["title"] == "B"  # 双词命中排前（coverage 主键）
    for h in hits:
        assert 0 < h["score"] <= 1  # score 正值 0..1，永不为 -0.0
        d = h["fts_detail"]
        assert isinstance(d["bm25_raw"], float) and d["bm25_raw"] < 0
        assert d["coverage_terms"] in ("1/2", "2/2")


def test_search_fts_explicit_and(ws_mod):
    """F1：显式 AND 仍要求双词并存，不做 OR 展开"""
    ws_mod.ws_ingest("库F1b", content="单一词内容，只有 alpha 出现。" * 5, title="A")
    ws_mod.ws_ingest("库F1b", content="alpha 与 beta 在这里同时出现。" * 5, title="B")
    s = ws_mod.ws_search("库F1b", "alpha AND beta", mode="fts")
    assert s["fts_query"] == '"alpha" AND "beta"'
    titles = {h["title"] for h in s["hits"]}
    assert titles == {"B"}


def test_search_fts_no_negative_zero(ws_mod):
    """F1：小语料常见词（IDF 钳制场景）score 不再是 -0.0"""
    ws_mod.ws_ingest("库F1c", content="知识库全文检索支持相关性排序与中文子串匹配。" * 30, title="常")
    s = ws_mod.ws_search("库F1c", "知识库 全文检索", mode="fts")
    assert s["total"] >= 1
    for h in s["hits"]:
        assert h["score"] is None or h["score"] > 0


# ---------- §8D 结构感知入库：标题锚点 / 源位置 / 章节聚合 ----------

def test_extract_headings_patterns():
    text = ("# " + "很长的合法标题" * 20 + "\n"        # markdown 标记不受行长限制
            + "第一章 总述\n" + "a" * 200 + "\n"
            + "第二节 细则\n" + "b" * 200 + "\n"
            + "条款3：具体条款内容说明\n" + "c" * 200 + "\n"
            + "3.2.1 编号小节标题\n" + "d" * 200 + "\n"
            + "普通句子以句号结尾不算标题。\n" + "e" * 300)
    hs = ws.extract_headings(text)
    by_text = {t: (lv, off) for off, lv, t in hs}
    assert any(t.startswith("很长的合法标题") for t in by_text)
    assert by_text["第一章 总述"][0] == 1 and by_text["第二节 细则"][0] == 2
    assert by_text["条款3：具体条款内容说明"][0] == 2
    assert by_text["3.2.1 编号小节标题"][0] == 3
    assert "普通句子以句号结尾不算标题。" not in by_text
    offs = [off for off, _, _ in hs]
    assert offs == sorted(offs) and offs[0] == 0


def test_extract_headings_running_header():
    """页眉/重复装饰自适应过滤：同文本 ≥3 次出现全部丢弃"""
    text = ("某书籍页眉\n" + "x" * 200 + "\n") * 5 + "第一章 真正标题\n" + "y" * 200
    hs = ws.extract_headings(text)
    assert all(t != "某书籍页眉" for _, _, t in hs)
    assert any(t == "第一章 真正标题" for _, _, t in hs)


def test_ingest_srcmap_pages_outline(ws_mod):
    """§8D：PDF 账本入库 → outline 成标题锚点 + source_loc 页码出口"""
    seg1 = "第1页文本内容，讨论主题甲。" * 20
    text = seg1 + "第2页文本内容，讨论主题乙。" * 20
    pages = [[0, 1], [len(seg1) + 1, 2]]
    outline = [[1, "第一章 主题甲", 1], [2, "第二章 主题乙", 2]]
    r = ws_mod.ws_ingest("库F2b", content=text, title="PDF书", source_type="pdf",
                         srcmap={"kind": "pdf", "pages": pages, "outline": outline})
    assert r["success"]
    s = ws_mod.ws_search("库F2b", "主题乙", mode="fts")
    assert s["total"] >= 1
    hit = s["hits"][0]
    assert hit["source_loc"] == {"kind": "pdf", "page": 2}   # 按命中位置归页（hit_offset），非分片起点
    assert hit["heading"]["text"] == "第二章 主题乙"          # 命中词真实所在章节
    # 标题路直接命中书签标题，且书签节锚到第二页
    s2 = ws_mod.ws_search("库F2b", "第二章", mode="fts")
    hd_hit = next(h for h in s2["hits"] if "heading" in (h.get("score_source") or ""))
    assert hd_hit["heading"]["text"] == "第二章 主题乙"
    assert hd_hit["source_loc"]["page"] == 2
    # section_ref 精读闭环
    r2 = ws_mod.ws_read_entry("库F2b", None, section=hd_hit["section_ref"])
    assert r2["success"] and "讨论主题乙" in r2["content"]
    assert r2["section"]["source_loc"]["page"] == 2


def test_ingest_md_headings_search_and_section_read(ws_mod):
    """§8D：markdown 标题解析 → 第三路命中 + heading/section 出口 + 精读边界"""
    text = ("# 条款14 使用reserve避免重新分配\n" + "正文甲内容讨论 reserve 机制细节。" * 30
            + "\n## 条款15 resize语义\n" + "正文乙内容讨论 resize 差异。" * 30)
    ws_mod.ws_ingest("库F2a", content=text, title="书")
    s = ws_mod.ws_search("库F2a", "条款14", mode="fts")
    assert s["total"] >= 1
    hit = s["hits"][0]
    assert hit["heading"]["text"].startswith("条款14")
    assert hit["section_ref"] and hit["section_chars"] > 0
    assert hit["source_loc"]["kind"] == "line" and hit["source_loc"]["n"] == 1
    r = ws_mod.ws_read_entry("库F2a", None, section=hit["section_ref"])
    assert r["success"] and r["content"].startswith("# 条款14")
    assert "条款15" not in r["content"]           # 整节边界：不含下一节标题


def test_section_aggregation_union_source(ws_mod):
    """§8D：同条目同章节 fts+heading 双路命中合并，score_source 并列标注"""
    text = "# 条款7 讨论章\n" + "alpha 内容块叙述。" * 4600
    ws_mod.ws_ingest("库F2e", content=text, title="聚合", no_embed=True)
    s = ws_mod.ws_search("库F2e", "条款7 alpha", mode="fts")
    assert s["total"] >= 1
    merged = [h for h in s["hits"] if h.get("same_section_hits")]
    assert merged
    assert set(merged[0]["score_source"].split("+")) == {"fts", "heading"}


def test_reindex_rebuilds_headings(ws_mod):
    """§8D：--reindex 幂等补建标题锚点（老条目）"""
    ws_mod.ws_ingest("库F2c", content="# 条款9 概述\n" + "正文内容。" * 50, title="R")
    d = ws_mod.ws_dir("库F2c")
    con = ws_mod._connect(d / ws_mod.WORKSPACE_DB)
    con.execute("DELETE FROM headings")
    con.commit()
    r = ws_mod.ws_reindex("库F2c")
    assert r["success"] and r["headings_rows"] >= 1
    s = ws_mod.ws_search("库F2c", "条款9", mode="fts")
    assert any((h.get("heading") or {}).get("text", "").startswith("条款9")
               for h in s["hits"])


def test_verify_heading_checks(ws_mod):
    """§8D：--verify 锚点校验（offset 越界上报）"""
    ws_mod.ws_ingest("库F2d", content="# 条款1 概述\n" + "正文内容。" * 50, title="V")
    assert ws_mod.ws_verify("库F2d")["ok"]
    d = ws_mod.ws_dir("库F2d")
    con = ws_mod._connect(d / ws_mod.WORKSPACE_DB)
    con.execute("UPDATE headings SET offset=999999")
    con.commit()
    r = ws_mod.ws_verify("库F2d")
    assert not r["ok"]
    assert any(i["issue"] == "heading_offset_out_of_range" for i in r["issues"])


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
    # §8D：win_start/win_end 为 full.md 内部坐标，出口抹去（snippet+chunk_no 承载定位）
    assert "win_start" not in s["hits"][0] and s["hits"][0]["snippet"]


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


# ---------- v0.7.1 连接池管理：活性探测/重建/文件缺失守卫/退避重试 ----------

def test_pool_rebuild_after_unhealthy(ws_mod):
    r = ws_mod.ws_ingest("库P1", content="重建验证内容。", title="T")
    assert r["success"]
    con = ws_mod._connect(ws_mod.ws_dir("库P1") / "workspace.db")
    assert ws_mod._healthy(con)
    con.close()  # 模拟坏连接
    con2 = ws_mod._connect(ws_mod.ws_dir("库P1") / "workspace.db")
    assert con2 is not con and ws_mod._healthy(con2)
    assert con2.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1


def test_missing_db_file_guard(ws_mod):
    import os
    r = ws_mod.ws_ingest("库G2", content="文件缺失守卫验证。", title="G")
    assert r["success"]
    p = ws_mod.ws_dir("库G2") / "workspace.db"
    ws_mod._release_db(p)
    os.remove(p)
    # create=False：不静默新建空库（防外部删除后的陈旧读/数据消失假象）
    try:
        ws_mod._connect(p, create=False)
        raise AssertionError("应抛出 FileNotFoundError")
    except FileNotFoundError:
        pass
    r = ws_mod.ws_read_entry("库G2", "x")
    assert not r["success"]  # ws_dir 守卫 → ws_not_found


def test_db_call_retry_on_locked(ws_mod, monkeypatch):
    """§错误恢复：locked 类 OperationalError → 重建连接退避重试 2 次后成功"""
    import sqlite3 as s3
    r = ws_mod.ws_ingest("库R1", content="重试机制验证内容。" * 10, title="R")
    assert r["success"]
    calls = {"n": 0}
    real = ws_mod._fts_search_one

    def flaky(con, ws_name, match, like_terms, limit, phrases=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise s3.OperationalError("database is locked")
        return real(con, ws_name, match, like_terms, limit, phrases=phrases)
    monkeypatch.setattr(ws_mod, "_fts_search_one", flaky)
    s = ws_mod.ws_search("库R1", "重试机制", mode="fts")
    assert s["success"] and s["total"] >= 1 and calls["n"] == 2


def test_db_call_non_retryable_error(ws_mod, monkeypatch):
    """结构类错误（no such table）不重试，立即抛出"""
    import sqlite3 as s3
    ws_mod.ws_ingest("库R2", content="非重试错误验证。", title="N")
    calls = {"n": 0}

    def boom(con, ws_name, match, like_terms, limit, phrases=None):
        calls["n"] += 1
        raise s3.OperationalError("no such table: entries_fts")
    monkeypatch.setattr(ws_mod, "_fts_search_one", boom)
    try:
        ws_mod.ws_search("库R2", "验证", mode="fts")
        raise AssertionError("应抛出")
    except s3.OperationalError:
        pass
    assert calls["n"] == 1  # 未重试


def test_fts_hit_offset_section_attribution(ws_mod):
    """偏差2修复：FTS 命中的 heading/section 按 chunk 内命中位置归位，而非分片起点"""
    text = ("# 条款1 开篇章节\n" + "甲" * 41000
            + "\n# 条款2 目标章节\n" + "此处 reserve 关键内容。" * 60)
    ws_mod.ws_ingest("库R1", content=text, title="归位", no_embed=True)
    s = ws_mod.ws_search("库R1", "reserve", mode="fts")
    assert s["total"] >= 1
    hit = s["hits"][0]
    assert (hit.get("heading") or {}).get("text", "").startswith("条款2")  # 命中词真实所在章节
    assert hit["score_kind"] == "coverage"


def test_score_kind_stamping(ws_mod):
    """偏差1修复：score 语义由 score_kind 编程可判——fts=coverage、fused=rrf"""
    ws_mod.ws_ingest("库R2", content="score_kind 语义验证内容。" * 20, title="K", no_embed=True)
    s = ws_mod.ws_search("库R2", "score_kind", mode="fts")
    assert s["hits"][0]["score_kind"] == "coverage"
    s = ws_mod.ws_search("库R2", "score_kind", mode="fused")
    assert s["hits"][0]["score_kind"] == "rrf"


def test_column_filter_fts_only(ws_mod):
    """D1：列限定查询自动降级 FTS-only，向量/标题路不参与"""
    ws_mod.ws_ingest("库D1", content="语义路绕过验证内容 reserve。" * 30, title="某书籍")
    for mode in ("fts", "fused"):
        s = ws_mod.ws_search("库D1", "title:不存在的词xyz", mode=mode)
        assert s["column_filter"] is True and s["mode"] == "fts" and s["total"] == 0
    s = ws_mod.ws_search("库D1", "title:某书籍", mode="fused")
    assert s["total"] >= 1  # 正向：标题命中仍然可用（列值需 ≥3 字，trigram 物理限制）


def test_front_section_fallback(ws_mod):
    """S2：首个标题前的命中给 #front 哨兵引用，可 --section 精读"""
    text = "前言内容叙述，位于首个标题之前。" * 10 + "\n# 条款1 正文\n" + "正文内容叙述。" * 30
    ws_mod.ws_ingest("库S2", content=text, title="前言", no_embed=True)
    s = ws_mod.ws_search("库S2", "前言内容", mode="fts")
    hit = s["hits"][0]
    assert hit["section_ref"].endswith("#front") and hit["section_chars"] > 0
    eid = hit["entry_id"]
    r = ws_mod.ws_read_entry("库S2", None, section=eid + "#front")
    assert r["success"] and "首个标题之前" in r["content"]


def test_ws_embed_rebuild(ws_mod):
    """S3：--embed 为零向量条目补建向量（测试环境走旧 vectors 表回退）"""
    ws_mod.ws_ingest("库S3", content="补建向量验证内容 alpha。" * 30, title="E", no_embed=True)
    lst = ws_mod.ws_list_entries("库S3")
    assert lst["entries"][0]["vectors"] == 0
    r = ws_mod.ws_embed("库S3")
    assert r["success"] and r["embedded_entries"] == 1 and r["vectors"] > 0
    lst = ws_mod.ws_list_entries("库S3")
    assert lst["entries"][0]["vectors"] > 0
    s = ws_mod.ws_search("库S3", "alpha", mode="vector")
    assert s["total"] >= 1


def test_supersedes_and_replace(ws_mod, tmp_path):
    """S4：同源重入库提示 supersedes；--replace 自动清理旧条目"""
    d = tmp_path / "bookA.md"
    d.write_text("同源条目版本甲内容叙述。" * 50, encoding="utf-8")
    r1 = ws_mod.ws_ingest("库S4", content="同源条目版本甲内容叙述。" * 50,
                          title="同源书", source_ref="C:/x/bookA.md", no_embed=True)
    r2 = ws_mod.ws_ingest("库S4", content="同源条目版本乙内容叙述不同。" * 50,
                          title="同源书", source_ref="C:/x/bookA.md", no_embed=True)
    assert r2["success"] and not r2["updated"] and r1["entry_id"] in r2["supersedes"]
    r3 = ws_mod.ws_ingest("库S4", content="同源条目版本丙再变一次。" * 50,
                          title="同源书", source_ref="C:/x/bookA.md", no_embed=True, replace=True)
    assert r3["replaced"] and ws_mod.ws_verify("库S4")["ok"]
    ids = [e["id"] for e in ws_mod.ws_list_entries("库S4")["entries"]]
    assert r1["entry_id"] not in ids


def test_metadata_reuse_and_title_strip(ws_mod):
    """S5：同源重入库沿用未指定元数据；默认标题剥离扩展名"""
    ws_mod.ws_ingest("库S5", content="元数据复用验证内容。" * 30, title="原书名",
                     author="原作者", source_ref="C:/x/b.md", no_embed=True)
    ws_mod.ws_ingest("库S5", content="元数据复用验证内容新版不同。" * 30,
                     source_ref="C:/x/b.md", no_embed=True)
    e = ws_mod.ws_list_entries("库S5")["entries"][0]
    assert e["author"] == "原作者"
    assert ws_mod._entry_title_hint(None, "任意", "x")  # hint 存在


def test_n1_route_scores_invariant(ws_mod):
    """N1+R1：scores 为各路真实贡献（多窗口同 key 累加），score==Σscores 恒成立"""
    ws_mod.ws_ingest("库N1", content="多窗口贡献验证 reserve alpha 内容。" * 400, title="N1")
    s = ws_mod.ws_search("库N1", "reserve alpha", mode="fused")
    assert s["total"] >= 1
    checked = 0
    for h in s["hits"]:
        if "scores" in h:
            assert abs(h["score"] - round(sum(h["scores"].values()), 4)) < 1e-3
            assert all(v > 0 for v in h["scores"].values())
            checked += 1
    assert checked >= 1


def test_n2_media_timestamp_override(ws_mod):
    """N2：命中时间戳精确到命中词所在段落（time 账本覆写 chunk 级全文件区间）"""
    segs = [{"text": "开场白内容叙述甲。" * 30, "start_ms": 0, "end_ms": 20000},
            {"text": "关键主题 reserve 讨论段乙。" * 30, "start_ms": 60000, "end_ms": 90000},
            {"text": "结尾收束内容丙。" * 30, "start_ms": 120000, "end_ms": 150000}]
    ws_mod.ws_ingest("库N2", segments=segs, title="音频", source_type="audio", no_embed=True)
    s = ws_mod.ws_search("库N2", "reserve 讨论段", mode="fts")
    assert s["total"] >= 1
    h = s["hits"][0]
    assert 60000 <= h["start_ms"] < 90000          # 覆写为命中词所在段，而非 0~150000
    assert h["timestamp_precision"] == "segment"
    assert h["source_loc"]["kind"] == "time"


def test_r4_windowed_section_read(ws_mod):
    """R4：section_ref 携带命中偏移，开窗返回含命中词的内容"""
    text = "# 条款A 标题\n" + "甲" * 120000 + "\n此处 reserve 关键内容深藏。\n" + "乙" * 120000
    ws_mod.ws_ingest("库N3", content=text, title="长节", no_embed=True)
    s = ws_mod.ws_search("库N3", "reserve", mode="fts")
    ref = s["hits"][0]["section_ref"]
    assert "@" in ref                              # 命中偏移已内嵌
    r = ws_mod.ws_read_entry("库N3", None, section=ref, max_chars=30000)
    assert r["success"] and "reserve" in r["content"]   # 开窗以命中为中心，内容含命中词


def test_n3_read_cap_truncated(ws_mod, tmp_path):
    """N3+R2：entry/chunk/section 读路径统一截断标注 truncated/remaining_chars"""
    text = "# 节甲\n" + "长" * 40000
    ws_mod.ws_ingest("库N3b", content=text, title="截断", no_embed=True)
    eid = ws_mod.ws_list_entries("库N3b")["entries"][0]["id"]
    r = ws_mod.ws_read_entry("库N3b", eid, max_chars=1000)
    assert r["truncated"] and r["total_chars"] >= 40000 and r["remaining_chars"] > 0
    assert len(r["content"]) <= 1000
    r0 = ws_mod.ws_read_entry("库N3b", eid, max_chars=0)   # 0=不限
    assert "truncated" not in r0


def test_vector_zero_hint(ws_mod):
    """坑4：--mode vector 零命中且库内无向量 → 可诊断标注（vectors_rows/vector_zero_hint）"""
    ws_mod.ws_ingest("库V0", content="向量缺失诊断验证内容。" * 30, title="V", no_embed=True)
    s = ws_mod.ws_search("库V0", "向量缺失", mode="vector")
    assert s["success"] and s["vectors_rows"] == 0 and s.get("vector_zero_hint") is True
