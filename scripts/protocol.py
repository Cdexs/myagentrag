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
    默认播放器/文件关联）。返回结构化结果。"""
    exe = sys.executable
    script = _extract_py_path()
    if os.name == "nt":
        import subprocess
        handler = f'"{exe}" "{script}" --play-uri "%1"'
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
                "handler": handler, "message": pair[messages.get_lang()],
                "message_i18n": pair}
    if sys.platform.startswith("linux"):
        apps = Path.home() / ".local" / "share" / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        desktop = apps / "myagentrag-play.desktop"
        desktop.write_text(
            "[Desktop Entry]\nType=Application\nName=MyAgentRAG Play\n"
            f"Exec={exe} {script} --play-uri %u\n"
            "NoDisplay=true\nTerminal=false\n"
            f"MimeType=x-scheme-handler/{SCHEME};\n", encoding="utf-8")
        import subprocess
        subprocess.run(["xdg-mime", "default", desktop.name,
                        f"x-scheme-handler/{SCHEME}"], capture_output=True)
        pair = messages.msg_pair("protocol_registered")
        return {"success": True, "protocol": SCHEME, "registered": True,
                "handler": str(desktop), "message": pair[messages.get_lang()],
                "message_i18n": pair}
    pair = messages.msg_pair("protocol_unsupported", os=sys.platform)
    return {"success": False, "protocol": SCHEME, "registered": False,
            "error": pair[messages.get_lang()], "error_i18n": pair}


def unregister_protocol():
    """反注册 myagentrag:// 协议处理器（移除 skill 自有命名空间）。"""
    if os.name == "nt":
        import subprocess
        r = subprocess.run(["reg", "delete", _REG_KEY, "/f"],
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            pair = messages.msg_pair("protocol_register_failed", err=err[:160])
            return {"success": False, "protocol": SCHEME, "registered": False,
                    "unregistered": False, "error": pair[messages.get_lang()],
                    "error_i18n": pair}
        pair = messages.msg_pair("protocol_unregistered")
        return {"success": True, "protocol": SCHEME, "registered": False,
                "unregistered": True, "message": pair[messages.get_lang()],
                "message_i18n": pair}
    if sys.platform.startswith("linux"):
        desktop = Path.home() / ".local" / "share" / "applications" / "myagentrag-play.desktop"
        desktop.unlink(missing_ok=True)
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
