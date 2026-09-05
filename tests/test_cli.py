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
    r = run(cli_env, "--file", str(doc), "--workspace", "CLI库",
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
    r = run(cli_env, "--file", str(doc), "--workspace", "CLI库2")
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
