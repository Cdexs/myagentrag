# -*- coding: utf-8 -*-
"""技能文档访问（SKILL.md 契约核心 + references/ 引用文件定位）

为什么单独成模块：v0.1.2 起 SKILL.md 拆分为主文件（不可豁免契约）+ references/
（细节）。调用方 agent 可能只加载到主文件片段，因此需要两条兜底通道——
① `--contract`：直接从 SKILL.md 抽取 CONTRACT-CORE 块打印（文档被截断也能拿到规则）；
② 检索/精读输出携带 `refs_dir` 绝对路径 + `next_required_step`，让"该读哪份细节
   文件"这件事由工具输出直接给出，不依赖文档是否被完整读到。

依赖方向：skilldoc ← extract / workspace（只读文件，不import其他脚本模块）。
"""
import re
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent      # 技能根目录（含 SKILL.md）
_CORE_RE = re.compile(r"<!-- CONTRACT-CORE -->(.*?)<!-- /CONTRACT-CORE -->", re.S)

# 引用文件名 → 用途（与 SKILL.md 触发表一致；改列表须同步守卫测试）
REFS = {
    "presentation-contract.md": "full presentation contract (structure, templates, depths, self-checks, examples)",
    "retrieval.md": "retrieval mechanics (limit/filters/syntax/scores), read paths, seeked playback, phrasebook",
    "ingest-and-workspace.md": "ingesting (files/URLs/batch), metadata, management commands, layout & migration",
    "setup-and-deps.md": "runtime install, ffmpeg/whisper/GPU, temp dir, cookies",
    "troubleshooting.md": "error table, exit codes, confirmation flows",
}


def skill_dir():
    return _SKILL_DIR


def skill_md_path():
    return _SKILL_DIR / "SKILL.md"


def refs_dir():
    """references/ 目录（可能不存在于被裁剪的安装中——调用方需容忍）"""
    return _SKILL_DIR / "references"


def contract_core():
    """SKILL.md 的 CONTRACT-CORE 块（不可豁免核心）；缺失/不可读返回 ""。"""
    try:
        text = skill_md_path().read_text(encoding="utf-8")
    except OSError:
        return ""
    m = _CORE_RE.search(text)
    return m.group(1).strip() if m else ""


def ref_path(name):
    """引用文件的绝对路径（字符串）；文件不存在返回 None。

    为什么是绝对路径：消费方是调用 agent 的文件读取工具，其工作目录是用户项目
    目录，相对路径会解析失败；环境变量形态也无法被读取工具展开。CLI 用 __file__
    自身位置解析，因此永远指向正在运行的这份安装（移动技能目录后无需改配置）。
    相对形态（references/<name>）只用于人读、日志与转发，见 ref_rel()。"""
    p = refs_dir() / name
    return str(p) if p.is_file() else None


def ref_rel(name):
    """引用文件的相对形态（相对技能根目录；人读、日志与转发用，不可直接交给读取工具）"""
    return "references/" + name


def missing_refs():
    """安装不完整时用于自检：期望存在但缺失的引用文件清单"""
    return [n for n in REFS if not (refs_dir() / n).is_file()]
