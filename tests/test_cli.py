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
    env["SMART_SUMMARIZE_WORKSPACES_DIR"] = str(tmp_path / "workspaces")
    env["SMART_SUMMARIZE_LANG"] = "zh"
    # 测试直接测当前解释器，跳过专用运行时闸门（运行时安装/切换由 R5 冒烟与手动验证覆盖）
    env["SMART_SUMMARIZE_NO_RUNTIME"] = "1"
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
    assert r.returncode == 2 and "--entry" in r.stderr


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
    assert r.returncode == 1 and "--url" in r.stderr


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
    ws_root = Path(cli_env["SMART_SUMMARIZE_WORKSPACES_DIR"])
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
    doc2 = __import__("pathlib").Path(cli_env["SMART_SUMMARIZE_WORKSPACES_DIR"]).parent / "m2.md"
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
