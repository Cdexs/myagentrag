# -*- coding: utf-8 -*-
"""myagentrag:// 协议与定位播放模块

职责（独立于 extract.py 主脚本，协议处理与播放回调不进主流程）：
- parse_play_uri：`myagentrag://play?ws=&entry=&at=` 的解析与白名单校验
  （entry 限 16 位十六进制阻断路径穿越；参数白名单拒绝多余/缺失键；
  at 数值区间限制——任何非法输入抛 ValueError，绝不进入命令拼接）
- register_protocol / unregister_protocol：Windows 写 HKCU 用户级注册表的
  skill 自有命名空间（Software\\Classes\\myagentrag），Linux 写用户级
  .desktop；**不触碰任何系统默认播放器与文件关联**，可完全反注册
- handle_play_uri：校验通过后复用 workspace.ws_play（ffplay 定位播放）
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import messages

SCHEME = "myagentrag"
_ACTION = "play"
_ALLOWED_PARAMS = {"ws", "entry", "at"}
_ENTRY_RE = re.compile(r"^[0-9a-f]{16}$")
_WS_RE = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff_-]+$")
_MAX_AT_S = 24 * 3600

# Windows 注册表根键（skill 自有命名空间；测试可替换为隔离子键）
_REG_KEY = r"HKCU\Software\Classes\myagentrag"


def _extract_py_path():
    return Path(__file__).resolve().parent / "extract.py"


# 自定位启动器（写入受管目录 protocol/，注册表只绑定它——不写死源码/技能目录）。
# 技能目录解析顺序：MYAGENTRAG_SKILL_DIR 环境变量 → 同目录 skill.json（注册时记录）。
_LAUNCHER_SRC = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MyAgentRAG myagentrag:// 播放链接启动器（由 extract.py --register-protocol 生成，勿手改）。

注册表/.desktop 只绑定本文件（位于受管目录 protocol/ 下）与运行时 python；
技能目录不在注册项里写死，按以下顺序运行时解析：
  1) 环境变量 MYAGENTRAG_SKILL_DIR（移动技能目录后设置它即可，无需重注册）
  2) 同目录 skill.json 的 skill_dir（--register-protocol 记录，可重跑刷新）
  3) 均不可用：stderr 给出修复指引并返回 1
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    uri = sys.argv[1] if len(sys.argv) > 1 else ""
    skill = os.environ.get("MYAGENTRAG_SKILL_DIR") or ""
    if not skill:
        try:
            cfg = json.loads((Path(__file__).resolve().parent / "skill.json")
                             .read_text(encoding="utf-8"))
            skill = cfg.get("skill_dir") or ""
        except Exception:
            skill = ""
    extract = Path(skill) / "scripts" / "extract.py" if skill else None
    if not extract or not extract.is_file():
        print("myagentrag: 技能目录不可用——设置 MYAGENTRAG_SKILL_DIR 指向技能根目录，"
              "或重跑 extract.py --register-protocol 刷新绑定", file=sys.stderr)
        return 1
    return subprocess.call([sys.executable, str(extract), "--play-uri", uri])


if __name__ == "__main__":
    sys.exit(main())
'''


def _protocol_dir():
    import deps
    d = deps.MANAGED_HOME / "protocol"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_launcher():
    """写入自定位启动器 + 技能目录配置（受管目录内）。
    返回 (launcher_path, skill_dir)。"""
    import json
    pdir = _protocol_dir()
    launcher = pdir / "play.py"
    launcher.write_text(_LAUNCHER_SRC, encoding="utf-8")
    skill_dir = _extract_py_path().parent.parent   # 技能根目录（含 scripts/extract.py）
    (pdir / "skill.json").write_text(
        json.dumps({"skill_dir": str(skill_dir)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return launcher, skill_dir


def parse_play_uri(uri):
    """解析并校验 myagentrag://play URI → (ws_name, entry_id, at_s)。

    at 支持整数或小数秒（内部取整到秒，与 --play 的秒级定位一致）。
    任何非法输入抛 ValueError（文案含原因）；绝不返回未校验内容。"""
    p = urlparse(uri)
    if p.scheme != SCHEME or p.netloc != _ACTION:
        raise ValueError(f"未知协议: {p.scheme}://{p.netloc}")
    qs = parse_qs(p.query, keep_blank_values=True)
    keys = set(qs.keys())
    extra = keys - _ALLOWED_PARAMS
    if extra:
        raise ValueError(f"未知参数: {', '.join(sorted(extra))}")
    missing = _ALLOWED_PARAMS - keys
    if missing:
        raise ValueError(f"缺少参数: {', '.join(sorted(missing))}")
    ws = qs["ws"][0].strip()
    entry = qs["entry"][0].strip().lower()
    at = qs["at"][0].strip()
    if not ws or not _WS_RE.fullmatch(ws):
        raise ValueError(f"库名不合法: {ws!r}")
    if not _ENTRY_RE.fullmatch(entry):
        raise ValueError(f"entry 非法（须 16 位十六进制）: {entry!r}")
    try:
        at_s = float(at) if "." in at else int(at)
    except ValueError:
        raise ValueError(f"at 非数值: {at!r}")
    if not (0 <= at_s <= _MAX_AT_S):
        raise ValueError(f"at 超出范围（0..24h）: {at}")
    return ws, entry, int(round(at_s))


def register_protocol():
    """注册 myagentrag:// 协议处理器（仅 skill 自有命名空间，不触碰系统
    默认播放器/文件关联）。

    注册项只绑定受管目录内的自定位启动器（`<home>/protocol/play.py`）+ 运行时
    python——**不写死源码/技能目录**；技能目录经 MYAGENTRAG_SKILL_DIR 环境变量
    或启动器旁 skill.json（本次记录）运行时解析，移动目录不会写死失效。"""
    exe = sys.executable
    launcher, skill_dir = _write_launcher()
    hint = messages.msg_pair("protocol_skill_dir_hint", dir=str(skill_dir))
    if os.name == "nt":
        import subprocess
        handler = f'"{exe}" "{launcher}" "%1"'
        cmds = [
            ["reg", "add", _REG_KEY, "/ve", "/t", "REG_SZ",
             "/d", "URL:myagentrag play", "/f"],
            ["reg", "add", _REG_KEY, "/v", "URL Protocol", "/t", "REG_SZ", "/d", "", "/f"],
            ["reg", "add", _REG_KEY + r"\shell\open\command", "/ve", "/t", "REG_SZ",
             "/d", handler, "/f"],
        ]
        for c in cmds:
            r = subprocess.run(c, capture_output=True, text=True, errors="replace")
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "reg add 失败").strip()
                pair = messages.msg_pair("protocol_register_failed", err=err[:160])
                return {"success": False, "protocol": SCHEME, "registered": False,
                        "error": pair[messages.get_lang()], "error_i18n": pair}
        pair = messages.msg_pair("protocol_registered")
        return {"success": True, "protocol": SCHEME, "registered": True,
                "handler": handler, "launcher": str(launcher),
                "skill_dir": str(skill_dir),
                "message": pair[messages.get_lang()], "message_i18n": pair,
                "hint": hint[messages.get_lang()], "hint_i18n": hint}
    if sys.platform.startswith("linux"):
        import subprocess
        apps = Path.home() / ".local" / "share" / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        desktop = apps / "myagentrag-play.desktop"
        desktop.write_text(
            "[Desktop Entry]\nType=Application\nName=MyAgentRAG Play\n"
            f"Exec={exe} {launcher} %u\n"
            "NoDisplay=true\nTerminal=false\n"
            f"MimeType=x-scheme-handler/{SCHEME};\n", encoding="utf-8")
        subprocess.run(["xdg-mime", "default", desktop.name,
                        f"x-scheme-handler/{SCHEME}"], capture_output=True)
        pair = messages.msg_pair("protocol_registered")
        return {"success": True, "protocol": SCHEME, "registered": True,
                "handler": str(desktop), "launcher": str(launcher),
                "skill_dir": str(skill_dir),
                "message": pair[messages.get_lang()], "message_i18n": pair,
                "hint": hint[messages.get_lang()], "hint_i18n": hint}
    pair = messages.msg_pair("protocol_unsupported", os=sys.platform)
    return {"success": False, "protocol": SCHEME, "registered": False,
            "error": pair[messages.get_lang()], "error_i18n": pair}


def unregister_protocol():
    """反注册 myagentrag:// 协议处理器（移除注册项与受管目录内的启动器文件）。"""
    if os.name == "nt":
        import shutil
        import subprocess
        r = subprocess.run(["reg", "delete", _REG_KEY, "/f"],
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            pair = messages.msg_pair("protocol_register_failed", err=err[:160])
            return {"success": False, "protocol": SCHEME, "registered": False,
                    "unregistered": False, "error": pair[messages.get_lang()],
                    "error_i18n": pair}
        shutil.rmtree(_protocol_dir(), ignore_errors=True)   # 启动器与记录一并移除
        pair = messages.msg_pair("protocol_unregistered")
        return {"success": True, "protocol": SCHEME, "registered": False,
                "unregistered": True, "message": pair[messages.get_lang()],
                "message_i18n": pair}
    if sys.platform.startswith("linux"):
        import shutil
        desktop = Path.home() / ".local" / "share" / "applications" / "myagentrag-play.desktop"
        desktop.unlink(missing_ok=True)
        shutil.rmtree(_protocol_dir(), ignore_errors=True)
        pair = messages.msg_pair("protocol_unregistered")
        return {"success": True, "protocol": SCHEME, "registered": False,
                "unregistered": True, "message": pair[messages.get_lang()],
                "message_i18n": pair}
    pair = messages.msg_pair("protocol_unsupported", os=sys.platform)
    return {"success": False, "protocol": SCHEME, "registered": False,
            "error": pair[messages.get_lang()], "error_i18n": pair}


def handle_play_uri(uri):
    """执行 myagentrag://play：校验 → 复用 workspace.ws_play（ffplay 定位播放）。"""
    import workspace
    try:
        ws, entry, at_s = parse_play_uri(uri)
    except ValueError as e:
        return messages.err_result("play_uri_invalid", err=str(e))
    return workspace.ws_play(ws, entry, str(at_s))
