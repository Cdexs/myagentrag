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
REFS_DIR = Path(__file__).resolve().parent.parent / "references"

SRT = """1
00:00:01,000 --> 00:00:03,500
知识库检索支持全文匹配

2
00:00:04,000 --> 00:00:06,000
并且可以定位到媒体时间戳
"""


@pytest.fixture(scope="module")
def main_text():
    """主文件（自动加载的那一份）"""
    return SKILL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def skill_text(main_text):
    """主文件 + references/ 全量拼接：条款守卫跨文件生效（细节已拆到 references）"""
    parts = [main_text]
    for f in sorted(REFS_DIR.glob("*.md")):
        parts.append(f.read_text(encoding="utf-8"))
    return chr(10).join(parts)


def test_contract_section_exists(skill_text):
    assert "Result presentation contract" in skill_text
    assert "source-links list" in skill_text


def test_contract_is_single_reply(skill_text):
    """呈现契约必须写明"一次提问一次答复给全"——命中列表与链接清单都在同一条
    回答里，不得让用户追问才补链接（防止被读成"多轮"语义）"""
    assert "a single reply" in skill_text, "契约需明确链接清单与命中列表同属一条回答"


def test_contract_prevents_content_compression(skill_text):
    """结论段防压缩条款齐备：完整性五类、详略档位、阈值边界声明、输出前自检——
    契约只约束"形式"不约束"信息量"时，agent 会把多要点压成一句（Gavin 侧实例）"""
    for key in ("Information completeness", "Detail levels", "Pre-output self-check",
                "does not spill over into the conclusion section",
                "replace a specific term with a broader one"):
        assert key in skill_text, f"契约缺少防压缩条款: {key}"


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
    assert "compatibility alias" in skill_text


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


def test_contract_requires_deep_read(skill_text):
    """取数深度：内容型提问必须精读，snippet 只是预览——防"拿预览当正文"
    （Gavin 侧实例：只用 snippet 预览对 OAHSPE 下了错误的定性结论）"""
    for key in ("Retrieval depth", "preview window",
                "The original text returned by `--section` deep reading",
                "deep-read first, then answer", "Retrieval-depth self-check",
                "Qualitative conclusions must rest on deep reading",
                "never answer from `snippet` alone"):
        assert key in skill_text, f"契约缺少取数深度条款: {key}"


def test_contract_documents_truncation_signal(skill_text):
    """截断信号必须写进契约与字段指南：candidates_total / truncated_by_limit /
    内容型必须 --limit 50 / len(hits)<limit 不代表未截断"""
    for key in ("candidates_total", "truncated_by_limit", "never the default 20",
                'does not mean "not truncated"', "Fix candidate truncation first"):
        assert key in skill_text, f"契约缺少截断信号条款: {key}"


def test_contract_documents_recall_depth(skill_text):
    """截断信号之外还要写明"召回深度随 limit 放大"——避免 agent 把
    truncated_by_limit=false 误读为"已拿全"（实测 20→19 / 50→28）"""
    for key in ("within the current recall depth", "a larger limit recovers deeper sources"):
        assert key in skill_text, f"契约缺少召回深度说明: {key}"


def test_heuristic_list_matches_code(skill_text):
    """裸文本标题启发式的文档清单须与 workspace._HEURISTIC_PATTERNS 实际口径一致，
    且不得再出现重复项（QA 复核发现的 P2 笔误 "Chapter N, Clause N, Chapter N"）"""
    for token in ("第N章", "第N节", "条款N", "Chapter N", "Section N", "1.2.3"):
        assert token in skill_text, f"文档启发式清单缺少实际支持的形态: {token}"
    assert "Chapter N, Clause N, Chapter N" not in skill_text


def test_source_links_are_gated_by_self_check(skill_text):
    """源链接清单必须进强制自检门禁（agent 反馈：它只是一条散落要求，
    没有自检拦截 → 长回答末尾常被省略）。要求：⑨-⑪ 三项存在且清单段落
    声明受其门禁。"""
    assert "Source-links self-check" in skill_text
    for token in ("⑨", "⑩", "⑪", "count check: number of link lines == number of distinct cited"):
        assert token in skill_text, f"源链接自检门禁缺少: {token}"
    assert "gated by self-check items ⑨–⑪" in skill_text


# ---------- v0.1.2 文档拆分：自动加载面必须"自我可诊断 + 自足" ----------

def test_main_file_is_small_enough(main_text):
    """主文件必须足够小以适配宿主注入窗口（拆分的目的就是让它整体可载）"""
    size = len(main_text.encode("utf-8"))
    assert size <= 10 * 1024, f"主文件 {size} B 超过 10 KB 预算——内容请移入 references/"


def test_end_of_skill_sentinel(main_text):
    """文末哨兵：残片自诊断的判据（agent 看不到它即说明被截断）"""
    assert main_text.rstrip().endswith("<!-- END-OF-SKILL -->")
    assert "END-OF-SKILL" in main_text[:2000]


def test_truncation_zone_carries_the_core(main_text):
    """宿主实测截断点约 1.8 KB：该区间内必须能读到"被截断"告警与最关键的规则，
    否则 agent 无法自知、也拿不到任何约束"""
    head = main_text[:1800]
    for token in ("END-OF-SKILL", "READ-FIRST", "--section", "Source links",
                  "--limit 50", "internal fields"):
        assert token in head, f"截断区缺少关键内容: {token}"


def test_contract_core_block_present_and_complete(main_text):
    """CONTRACT-CORE 块是 --contract 命令的数据源，必须在主文件里且含核心条目"""
    assert "<!-- CONTRACT-CORE -->" in main_text and "<!-- /CONTRACT-CORE -->" in main_text
    core = main_text.split("<!-- CONTRACT-CORE -->")[1].split("<!-- /CONTRACT-CORE -->")[0]
    for token in ("Three-step loop", "Source links", "Self-check", "limit 50"):
        assert token in core, f"CONTRACT-CORE 缺少: {token}"


def test_references_exist_and_are_reachable(main_text):
    """每份引用文件必须真实存在、非空，且在触发表中出现（够得着才谈得上"按需加载"）"""
    import skilldoc
    assert sorted(p.name for p in REFS_DIR.glob("*.md")) == sorted(skilldoc.REFS)
    for name in skilldoc.REFS:
        f = REFS_DIR / name
        assert f.is_file() and f.stat().st_size > 500, name
        assert len(f.read_text(encoding="utf-8").encode("utf-8")) <= 32 * 1024, f"{name} 超过 32 KB"
        assert name in main_text, f"主文件未指向 {name}"
        assert skilldoc.ref_path(name), f"skilldoc 解析不到 {name}"


def test_references_declare_read_when(mission_text=None):
    """每份引用开头须有 READ WHEN 行（被单独打开时能自我定位）"""
    for f in sorted(REFS_DIR.glob("*.md")):
        head = f.read_text(encoding="utf-8")[:400]
        assert "READ WHEN" in head, f.name
