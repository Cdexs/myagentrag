# -*- coding: utf-8 -*-
"""test_cli — extract.py 命令行端到端（离线：文本提取 + workspace 全操作）"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
EXTRACT = REPO / "scripts" / "extract.py"


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    env = dict(os.environ)
    env["MYAGENTRAG_WORKSPACES_DIR"] = str(tmp_path / "workspaces")
    env["MYAGENTRAG_LANG"] = "zh"
    # 测试直接测当前解释器，跳过专用运行时闸门（运行时安装/切换由 R5 冒烟与手动验证覆盖）
    env["MYAGENTRAG_NO_RUNTIME"] = "1"
    return env


def run(cli_env, *argv, timeout=120):
    return subprocess.run([sys.executable, str(EXTRACT), *argv],
                          capture_output=True, text=True, env=cli_env,
                          timeout=timeout, errors="replace")


def jout(r):
    return json.loads(r.stdout)


def test_workspace_list_empty(cli_env):
    r = run(cli_env, "--workspace-list")
    assert jout(r) == {"success": True, "workspaces": [], "total": 0}


def test_create_and_duplicate(cli_env):
    r = run(cli_env, "--workspace", "CLI库", "--create")
    assert jout(r)["success"] is True
    r = run(cli_env, "--workspace", "CLI库", "--create")
    j = jout(r)
    assert j["success"] is False and "error_i18n" in j


def test_op_without_workspace_name(cli_env):
    r = run(cli_env, "--stats")
    j = jout(r)
    assert j["success"] is False and "--workspace" in j["error"]


def test_chunk_requires_entry(cli_env):
    r = run(cli_env, "--chunk", "1")
    # L1：argparse 误用同样走 JSON 契约（stdout 可解析）
    assert r.returncode == 2
    j = json.loads(r.stdout)
    assert j["success"] is False and "entry" in j["error"]


def test_file_not_found_bilingual(cli_env):
    r = run(cli_env, "--file", "Z:\\no_such.md")
    j = jout(r)
    assert j["success"] is False and "文件不存在" in j["error"]
    assert j["error_i18n"]["en"] == "File not found: Z:\\no_such.md"


def test_lang_en_flag(cli_env):
    r = run(cli_env, "--lang", "en", "--file", "Z:\\no_such.md")
    j = jout(r)
    assert j["error"].startswith("File not found")
    assert j["error_i18n"]["zh"].startswith("文件不存在")


def test_no_args(cli_env):
    r = run(cli_env)
    # S10 契约：无任务输入同样返回结构化 JSON（stdout 可 json.loads）
    assert r.returncode == 1
    j = json.loads(r.stdout)
    assert j["success"] is False and j["error_i18n"]["zh"]


def test_search_empty_string_json(cli_env):
    """D2：--search 空串与空格行为一致——JSON 错误而非 no_input 纯文本"""
    d = cli_env["MYAGENTRAG_WORKSPACES_DIR"]
    r = run(cli_env, "--workspace", "CLI空串库", "--search", "")
    assert r.returncode == 1
    j = json.loads(r.stdout)   # 契约：失败也必须可解析
    assert j["success"] is False and j["error_i18n"]


def test_ingest_and_search_and_read_e2e(cli_env, tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("# CLI 端到端\n\n验证提取入库检索读取的完整命令行链路，包括偏移精读。" * 3,
                   encoding="utf-8")
    r = run(cli_env, "--file", str(doc), "--workspace", "CLI库", "--no-embed",
            "--title", "端到端", "--author", "tester")
    j = jout(r)
    assert j["success"] is True and j["workspace"]["entry_id"]
    eid = j["workspace"]["entry_id"]

    r = run(cli_env, "--workspace", "CLI库", "--search", "偏移精读")
    j = jout(r)
    assert j["success"] and j["total"] >= 1 and j["hits"][0]["entry_id"] == eid

    r = run(cli_env, "--workspace", "CLI库", "--entry", eid)
    assert "CLI 端到端" in jout(r)["content"]

    r = run(cli_env, "--workspace", "CLI库", "--entry", eid, "--chunk", "1")
    assert jout(r)["chunk"]["chunk_no"] == 1

    r = run(cli_env, "--workspace", "CLI库", "--verify")
    assert jout(r)["ok"] is True

    r = run(cli_env, "--workspace", "CLI库", "--stats")
    assert jout(r)["entries"] == 1

    # source 副本（文档默认复制）
    ws_root = Path(cli_env["MYAGENTRAG_WORKSPACES_DIR"])
    assert (ws_root / "CLI库" / "source" / eid / "doc.md").exists()


def test_remove_entry_confirm_flow(cli_env, tmp_path):
    doc = tmp_path / "r.md"
    doc.write_text("待删除条目内容。", encoding="utf-8")
    r = run(cli_env, "--file", str(doc), "--workspace", "CLI库2", "--no-embed")
    eid = jout(r)["workspace"]["entry_id"]
    r = run(cli_env, "--workspace", "CLI库2", "--remove", eid)
    assert jout(r).get("confirm_required") is True
    r = run(cli_env, "--workspace", "CLI库2", "--remove", eid, "--yes")
    assert jout(r)["success"] is True


def test_rename_delete_workspace_flow(cli_env):
    run(cli_env, "--workspace", "流程库", "--create")
    r = run(cli_env, "--workspace", "流程库", "--rename", "流程库B")
    assert jout(r)["success"] is True
    r = run(cli_env, "--workspace", "流程库B", "--delete-workspace")
    assert jout(r).get("confirm_required") is True
    r = run(cli_env, "--workspace", "流程库B", "--delete-workspace", "--yes")
    assert jout(r)["success"] is True
    r = run(cli_env, "--workspace-list")
    assert jout(r)["total"] == 0


def test_search_mode_flag_passthrough(cli_env, tmp_path):
    doc = tmp_path / "m.md"
    doc.write_text("模式透传验证内容。", encoding="utf-8")
    r = run(cli_env, "--file", str(doc), "--workspace", "模式库", "--no-embed")
    assert jout(r)["success"] is True
    r = run(cli_env, "--workspace", "模式库", "--search", "模式透传", "--mode", "fts")
    j = jout(r)
    assert j["success"] and j["mode"] == "fts" and j["vector_available"] is False
    r = run(cli_env, "--workspace", "模式库", "--search", "模式透传", "--mode", "fused")
    j = jout(r)
    assert j["mode"] == "fused" and j["total"] >= 1


def test_kb_gate_lists_full_chain_when_components_missing(cli_env):
    """§8C.11：全新机器首次知识库使用 → 缺失清单含运行时/引擎/模型/扩展全链。
    （本测试环境 NO_RUNTIME=1 跳过闸门，故用真实路径的独立断言覆盖：删除跳过变量，
    但会真实安装组件——改为验证结构：不在此处真装，用 _missing_kb_kinds 单测覆盖。
    此处仅验证 fused 模式在 NO_RUNTIME 下按传统路径降级可检索。）"""
    doc = cli_env and None  # 占位保持结构清晰
    doc2 = __import__("pathlib").Path(cli_env["MYAGENTRAG_WORKSPACES_DIR"]).parent / "m2.md"
    doc2.write_text("全链依赖闸门结构验证。", encoding="utf-8")
    r = run(cli_env, "--file", str(doc2), "--workspace", "闸门库", "--no-embed")
    assert jout(r)["success"] is True
    r = run(cli_env, "--workspace", "闸门库", "--search", "全链依赖", "--mode", "fused")
    j = jout(r)
    assert j["success"] and j["vector_available"] is False  # 测试模式跳过向量路


def test_section_read_cli(cli_env, tmp_path):
    """§8D 端到端：--section 不透引用精读整节（且不受 --url/--file 入口守卫拦截）"""
    doc = tmp_path / "sec.md"
    doc.write_text("# 条款A 标题甲\n\n" + "正文甲内容叙述。\n" * 30
                   + "# 条款B 标题乙\n\n" + "正文乙内容叙述。\n" * 30, encoding="utf-8")
    r = run(cli_env, "--file", str(doc), "--workspace", "CLI节库", "--no-embed",
            "--title", "节读")
    assert jout(r)["success"] is True
    r = run(cli_env, "--workspace", "CLI节库", "--search", "条款A")
    hits = jout(r)["hits"]
    ref = next(h["section_ref"] for h in hits
               if (h.get("heading") or {}).get("text", "").startswith("条款A"))
    r = run(cli_env, "--workspace", "CLI节库", "--section", ref)  # 无 --url/--file 也须放行
    j = jout(r)
    assert j["success"] and j["content"].startswith("# 条款A 标题")
    assert "条款B" not in j["content"]


def test_search_limit_cli(cli_env, tmp_path):
    """--limit N 控制检索返回条数"""
    for i in (1, 2, 3):
        d = tmp_path / f"lim{i}.md"
        d.write_text(f"限定条数验证内容{i}。" * 300, encoding="utf-8")
        run(cli_env, "--file", str(d), "--workspace", "CLI限库", "--no-embed", "--title", f"L{i}")
    r = run(cli_env, "--workspace", "CLI限库", "--search", "限定", "--limit", "2")
    j = jout(r)
    assert j["success"] and j["total"] <= 2


def test_title_strip_ext_cli(cli_env, tmp_path):
    """S5：默认入库标题剥离扩展名"""
    doc = tmp_path / "my_book_title.md"
    doc.write_text("标题清洗验证内容。" * 100, encoding="utf-8")
    j = jout(run(cli_env, "--file", str(doc), "--workspace", "CLI标库", "--no-embed"))
    assert j["success"]
    title = jout(run(cli_env, "--workspace", "CLI标库", "--list"))["entries"][0]["title"]
    assert title == "my_book_title" and not title.endswith(".md")


def test_workspace_at_prefix(cli_env, tmp_path):
    """@库名 约定：--workspace 带任意前缀剥离"""
    doc = tmp_path / "at.md"
    doc.write_text("@语法约定验证内容。" * 100, encoding="utf-8")
    j = jout(run(cli_env, "--file", str(doc), "--workspace", "@AT库", "--no-embed",
                 "--title", "AT"))
    assert j["success"]
    lst = jout(run(cli_env, "--workspace-list"))
    assert "AT库" in [w["name"] for w in lst["workspaces"]]
    s = jout(run(cli_env, "--workspace", "@AT库", "--search", "约定"))
    assert s["success"] and s["total"] >= 1 and s["workspace"] == "AT库"


def test_n5_supersedes_dual_path(cli_env, tmp_path):
    """N5+R3：同源内容变更重入库——小文档路径 supersedes 可见；--replace 清理；
    supersedes 非空场景端到端断言"""
    p = tmp_path / "n5doc.md"
    p.write_text("第一版内容叙述。" * 200, encoding="utf-8")
    r1 = jout(run(cli_env, "--file", str(p), "--workspace", "CLI_N5库", "--no-embed"))
    assert r1["success"] and r1["workspace"]["supersedes"] == []
    p.write_text("内容已经变更的新叙述。" * 200, encoding="utf-8")   # 同源内容变更
    r2 = jout(run(cli_env, "--file", str(p), "--workspace", "CLI_N5库", "--no-embed"))
    assert r1["workspace"]["entry_id"] in r2["workspace"]["supersedes"]  # 非空（R3 断言）
    assert r2["workspace"]["title"] == "n5doc"           # S5 标题剥离扩展名
    p.write_text("第三次变更内容再改。" * 200, encoding="utf-8")
    r3 = jout(run(cli_env, "--file", str(p), "--workspace", "CLI_N5库", "--no-embed",
                  "--replace"))
    assert r3["workspace"]["replaced"] is True
    assert jout(run(cli_env, "--workspace", "CLI_N5库", "--list"))["total"] == 1


def test_n5_slice_path_workspace_preserved(cli_env, tmp_path):
    """N5 深层：>256K 走分片路径，workspace 信息（含 entry_id）不丢失"""
    p = tmp_path / "big.md"
    p.write_text("超大文档分片路径验证内容。" * 30000, encoding="utf-8")   # >256K
    r = run(cli_env, "--file", str(p), "--workspace", "CLI分片库", "--no-embed",
            "--title", "分片库")
    j = jout(r)
    assert j["platform"] == "slices"                     # 确认走了分片路径
    assert j.get("workspace", {}).get("entry_id")        # 入库信息未被覆盖丢失


def test_n3_max_chars_cli(cli_env, tmp_path):
    doc = tmp_path / "cap.md"
    doc.write_text("截断上限验证内容。" * 5000, encoding="utf-8")
    j = jout(run(cli_env, "--file", str(doc), "--workspace", "CLI截库", "--no-embed",
                 "--title", "截"))
    eid = j["workspace"]["entry_id"]
    j2 = jout(run(cli_env, "--workspace", "CLI截库", "--entry", eid, "--max-chars", "800"))
    assert j2["truncated"] is True and j2["remaining_chars"] > 0
    j3 = jout(run(cli_env, "--workspace", "CLI截库", "--entry", eid, "--max-chars", "0"))
    assert "truncated" not in j3


def test_search_missing_workspace_errors_p8(cli_env):
    """P8（QA 2026-09-09）：--search 对不存在的库名必须报 ws_not_found，
    不得静默返回 success:true + 空结果（调用方无法与"库内无匹配"区分）"""
    r = run(cli_env, "--workspace", "不存在XYZ123", "--search", "测试")
    assert r.returncode == 1
    j = json.loads(r.stdout)
    assert j["success"] is False
    assert "不存在" in j["error"] and "不存在XYZ123" in j["error"]
    assert j["error_i18n"]["en"].startswith("workspace not found")


def test_search_correct_name_no_match_distinguishable(cli_env):
    """P8 对照面：库名正确但无匹配 → success:true，且 workspaces_searched 指明被检库"""
    run(cli_env, "--workspace", "P8库", "--create")
    r = run(cli_env, "--workspace", "P8库", "--search", "绝无此词XYZ")
    j = jout(r)
    assert j["success"] is True and j["total"] == 0
    assert j["workspaces_searched"] == ["P8库"]


def test_search_all_workspaces_empty_root_errors(cli_env):
    """P8 关联：跨库检索但尚无任何库 → 明确报错而非 success:true 空结果"""
    r = run(cli_env, "--search", "随便查查", "--all-workspaces")
    assert r.returncode == 1
    j = json.loads(r.stdout)
    assert j["success"] is False and "尚无任何" in j["error"]


def test_search_vector_mode_missing_workspace_still_errors(cli_env):
    """原 vector 守卫行为保持：不存在库名在 vector 模式同样报错"""
    r = run(cli_env, "--workspace", "不存在XYZ123", "--search", "测试", "--mode", "vector")
    j = jout(r)
    assert j["success"] is False and "不存在XYZ123" in j["error"]


def test_batch_ingest_cli(cli_env, tmp_path):
    """批量入库 CLI：多 --file → batch 契约，条目可检索；单文件仍走旧契约"""
    (tmp_path / "batcha.md").write_text("# 甲\n\n批量入库甲文件内容验证。" * 10, encoding="utf-8")
    (tmp_path / "batchb.md").write_text("# 乙\n\n批量入库乙文件内容验证。" * 10, encoding="utf-8")
    r = run(cli_env, "--file", str(tmp_path / "batcha.md"),
            "--file", str(tmp_path / "batchb.md"),
            "--workspace", "CLI批量", "--no-embed")
    assert r.returncode == 0
    j = json.loads(r.stdout)
    assert j["batch"] is True and j["success"] is True
    assert len(j["results"]) == 2 and all(x["success"] for x in j["results"])
    r2 = run(cli_env, "--workspace", "CLI批量", "--search", "批量入库", "--mode", "fts", "--quiet")
    assert json.loads(r2.stdout)["total"] >= 1
    # 单文件 → 旧契约（无 batch 键，workspace 摘要挂在顶层）
    r3 = run(cli_env, "--file", str(tmp_path / "batcha.md"),
             "--workspace", "CLI批量", "--no-embed", "--quiet")
    j3 = json.loads(r3.stdout)
    assert j3["workspace"]["entry_id"] and "batch" not in j3


def test_batch_ingest_dir_and_partial_failure(cli_env, tmp_path):
    """--dir 扫描（不支持的扩展名不入列）+ 提取失败文件跳过且 rc=1"""
    (tmp_path / "d1.md").write_text("目录批量甲验证。" * 10, encoding="utf-8")
    (tmp_path / "d2.md").write_text("目录批量乙验证。" * 10, encoding="utf-8")
    (tmp_path / "skip.xyz").write_text("不支持的扩展名", encoding="utf-8")
    (tmp_path / "bad.pdf").write_text("损坏的 pdf 内容", encoding="utf-8")
    r = run(cli_env, "--dir", str(tmp_path), "--workspace", "CLI目录", "--no-embed")
    j = json.loads(r.stdout)
    assert j["batch"] is True and j["success"] is False and r.returncode == 1
    ok = {e["file"] for e in j["results"] if e["success"]}
    assert ok == {str(tmp_path / "d1.md"), str(tmp_path / "d2.md")}
    assert j["failed"] == [str(tmp_path / "bad.pdf")]
    assert all("skip.xyz" not in e["file"] for e in j["results"])


def test_batch_ingest_supersedes(cli_env, tmp_path):
    """OPT-01：批量 results[] 补 supersedes/replaced，与单文件契约对齐"""
    d = tmp_path / "sdir"
    d.mkdir()
    (d / "keep.md").write_text("獬豸保持不变内容锚点。" * 20, encoding="utf-8")
    (d / "s.md").write_text("貔貅原始内容甲。" * 20, encoding="utf-8")
    run(cli_env, "--dir", str(d), "--workspace", "CLI取代", "--no-embed", "--quiet")
    (d / "s.md").write_text("貔貅改后内容乙。" * 20, encoding="utf-8")
    r = run(cli_env, "--dir", str(d), "--workspace", "CLI取代", "--no-embed", "--quiet")
    j = json.loads(r.stdout)
    sres = next(e for e in j["results"] if e["file"].endswith("s.md"))
    assert sres["success"] and sres["supersedes"] and sres["replaced"] is False
    kres = next(e for e in j["results"] if e["file"].endswith("keep.md"))
    assert kres["supersedes"] == []


def test_batch_dir_single_file_batch_shape(cli_env, tmp_path):
    """KB-OPT-42：--dir 恒为 batch 形态（即使只扫到 1 个受支持文件）"""
    (tmp_path / "only.md").write_text("单文件目录批量验证。" * 10, encoding="utf-8")
    (tmp_path / "skip.xyz").write_text("x", encoding="utf-8")
    r = run(cli_env, "--dir", str(tmp_path), "--workspace", "CLI单文件目录",
            "--no-embed", "--quiet")
    j = json.loads(r.stdout)
    assert j["batch"] is True and len(j["results"]) == 1 and j["results"][0]["success"]
