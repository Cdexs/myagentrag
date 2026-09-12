# -*- coding: utf-8 -*-
"""test_protocol — myagentrag:// 解析校验 / 协议注册 / play-uri 执行（v0.1.1）"""
import os
import subprocess

import pytest

import protocol


# ---------- parse_play_uri 校验矩阵（防注入） ----------

def test_parse_play_uri_valid():
    ws, entry, at = protocol.parse_play_uri(
        "myagentrag://play?ws=%E6%88%91%E7%9A%84%E4%B9%A6%E6%9E%B6"
        "&entry=0123456789abcdef&at=624")
    assert ws == "我的书架" and entry == "0123456789abcdef" and at == 624


def test_parse_play_uri_decimal_at_rounds_to_second():
    _, _, at = protocol.parse_play_uri(
        "myagentrag://play?ws=lib&entry=0123456789abcdef&at=624.34")
    assert at == 624


@pytest.mark.parametrize("uri", [
    "http://play?ws=lib&entry=0123456789abcdef&at=1",                # 未知 scheme
    "myagentrag://open?ws=lib&entry=0123456789abcdef&at=1",          # 未知 action
    "myagentrag://play?ws=lib&entry=0123456789abcdef&at=1&x=1",      # 多余参数
    "myagentrag://play?ws=lib&entry=0123456789abcdef",               # 缺 at
    "myagentrag://play?entry=0123456789abcdef&at=1",                 # 缺 ws
    "myagentrag://play?ws=lib&at=1",                                 # 缺 entry
    "myagentrag://play?ws=lib&entry=../../etc/passwd&at=1",          # 路径穿越
    "myagentrag://play?ws=lib&entry=0123456789abcde&at=1",           # entry 长度错
    "myagentrag://play?ws=lib&entry=zzzzzzzzzzzzzzzz&at=1",          # entry 非 hex
    "myagentrag://play?ws=lib&entry=0123456789abcdef&at=$-rm",       # at 注入
    "myagentrag://play?ws=lib&entry=0123456789abcdef&at=-5",         # at 负值
    "myagentrag://play?ws=lib&entry=0123456789abcdef&at=999999",     # at 超 24h
    "myagentrag://play?ws=lib&entry=0123456789abcdef&at=abc",        # at 非数值
    "myagentrag://play?ws=bad/name&entry=0123456789abcdef&at=1",     # 库名非法字符
    "myagentrag://play?ws=&entry=0123456789abcdef&at=1",             # 空库名
])
def test_parse_play_uri_rejects(uri):
    with pytest.raises(ValueError):
        protocol.parse_play_uri(uri)


# ---------- handle_play_uri ----------

def test_handle_play_uri_invalid_structured():
    r = protocol.handle_play_uri("myagentrag://play?ws=lib&entry=bad&at=1")
    assert r["success"] is False and "error_i18n" in r and "不合法" in r["error"]


def test_handle_play_uri_reuses_ws_play(monkeypatch):
    """校验通过后复用 workspace.ws_play（不重复实现播放逻辑）"""
    import workspace
    captured = {}

    def fake_play(ws, entry, at, **kw):
        captured.update(ws=ws, entry=entry, at=at)
        return {"success": True}

    monkeypatch.setattr(workspace, "ws_play", fake_play)
    r = protocol.handle_play_uri(
        "myagentrag://play?ws=lib&entry=0123456789abcdef&at=63")
    assert r["success"] is True
    assert captured == {"ws": "lib", "entry": "0123456789abcdef", "at": "63"}


# ---------- 协议注册（Windows 真实注册表往返，隔离测试子键） ----------

@pytest.mark.skipif(os.name != "nt", reason="Windows 注册表路径")
def test_protocol_register_unregister_windows(monkeypatch):
    test_key = r"HKCU\Software\Classes\myagentrag-pytest"
    monkeypatch.setattr(protocol, "_REG_KEY", test_key)
    subprocess.run(["reg", "delete", test_key, "/f"], capture_output=True)
    try:
        r = protocol.register_protocol()
        assert r["success"] and r["registered"] is True
        assert "--play-uri" in r["handler"] and "extract.py" in r["handler"]
        q = subprocess.run(["reg", "query", test_key + r"\shell\open\command"],
                           capture_output=True, text=True, errors="replace")
        assert q.returncode == 0 and "--play-uri" in q.stdout
        u = protocol.unregister_protocol()
        assert u["success"] and u["unregistered"] is True
        q2 = subprocess.run(["reg", "query", test_key], capture_output=True, text=True, errors="replace")
        assert q2.returncode != 0      # 自有命名空间已完全移除
    finally:
        subprocess.run(["reg", "delete", test_key, "/f"], capture_output=True)
