# -*- coding: utf-8 -*-
"""test_docs_contract — SKILL.md「结果呈现契约」与运行期 locator 输出的一致性守卫

背景：呈现契约是 agent 侧的硬性输出规范，若文档与代码漂移（例如链接已统一到
myagentrag://goto 而示例/渲染规则仍写旧形态），各调用 agent 会按过期规则渲染。
本文件把「文档承诺」与「实际输出字段」钉在一起。
"""
import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / "SKILL.md"

SRT = """1
00:00:01,000 --> 00:00:03,500
知识库检索支持全文匹配

2
00:00:04,000 --> 00:00:06,000
并且可以定位到媒体时间戳
"""


@pytest.fixture(scope="module")
def skill_text():
    return SKILL.read_text(encoding="utf-8")


def test_contract_section_exists(skill_text):
    assert "结果呈现契约" in skill_text
    assert "源文链接清单" in skill_text


def test_no_rendered_legacy_alias(skill_text):
    """渲染规则与呈现示例不得再使用 myagentrag://play（旧别名只在
    「协议入口/兼容别名」处作为文字说明出现）"""
    rendered = re.findall(r"\]\((myagentrag://[^)]+)\)", skill_text)
    assert rendered, "契约示例应包含可点击链接"
    bad = [u for u in rendered if not u.startswith("myagentrag://goto?")]
    assert bad == [], f"渲染链接必须统一为 goto 入口，发现旧形态: {bad}"


def test_protocol_entry_documented(skill_text):
    """统一入口与兼容别名都在协议表中说明（供 agent 判断旧链接是否仍可用）"""
    assert "--goto-uri" in skill_text
    assert "兼容别名" in skill_text


@pytest.mark.parametrize("name", ["README.md", "README.en.md"])
def test_readme_documents_locator_link(name):
    """用户文档同步：README 必须写明统一协议入口的命令与链接形态，
    且不得再把旧别名当作可点击链接渲染"""
    text = (SKILL.parent / name).read_text(encoding="utf-8")
    assert "myagentrag://goto" in text, f"{name} 未说明统一定位链接"
    assert "--goto-uri" in text, f"{name} 未给出定位链接入口命令"
    rendered = re.findall(r"\]\((myagentrag://[^)]+)\)", text)
    assert [u for u in rendered if not u.startswith("myagentrag://goto?")] == []


def test_runtime_locator_shape_matches_contract(ws_mod, tmp_path, monkeypatch):
    """运行期 locator 字段与契约承诺一致：
    文档 {link(action=open), kind, target_label, open_scope, open(过渡)}
    媒体 {link(action=play), target_label, fallback_play_cmd}
    且两种链接都是 myagentrag://goto（agent 渲染规则可直接套用）"""
    monkeypatch.setattr(ws_mod.deps, "_find_ffplay", lambda: "/fake/ffplay")
    # 文档命中
    pdf = tmp_path / "契约书.pdf"
    pdf.write_bytes(b"%PDF-fake")
    seg1 = "第1页文本，讨论契约甲。" * 20
    text = seg1 + "第2页文本，讨论契约乙。" * 20
    r = ws_mod.ws_ingest("契约库", content=text, title="契约书", source_type="pdf",
                         source_ref=str(pdf),
                         srcmap={"kind": "pdf", "pages": [[0, 1], [len(seg1) + 1, 2]],
                                 "outline": [[1, "第一章", 1], [2, "第二章", 2]]})
    assert r["success"]
    doc = ws_mod.ws_search("契约库", "契约乙", mode="fts")["hits"][0]["locator"]
    assert {"link", "action", "kind", "target_label", "open_scope", "open"} <= set(doc)
    assert doc["action"] == "open"
    assert doc["link"].startswith("myagentrag://goto?")     # 契约渲染 `[打开原文件](link)`
    assert doc["open"].startswith("file:///")               # 过渡字段（旧版渲染兼容）
    assert doc["target_label"] == "第 2 页"
    # 媒体命中
    media = tmp_path / "契约录音.mp3"
    media.write_bytes(b"fake")
    r2 = ws_mod.ws_ingest("契约库2", srt_text=SRT, title="契约录音", source_type="audio",
                          source_file=str(media))
    assert r2["success"]
    med = ws_mod.ws_search("契约库2", "全文匹配", mode="fts")["hits"][0]["locator"]
    assert {"link", "action", "target_label", "fallback_play_cmd"} <= set(med)
    assert med["action"] == "play"
    assert med["link"].startswith("myagentrag://goto?")     # 契约渲染 `[▶ 从 mm:ss 播放](link)`
    assert "at=" in med["link"] and re.fullmatch(r"\d+:\d{2}", med["target_label"])
    # 读路径同形（精读轮次的清单也照同一套规则渲染）
    read = ws_mod.ws_read_entry("契约库", None, section=(
        ws_mod.ws_search("契约库", "契约乙", mode="fts")["hits"][0]["section_ref"]))
    assert read["locator"]["link"].startswith("myagentrag://goto?")
    assert read["locator"]["action"] == "open"
