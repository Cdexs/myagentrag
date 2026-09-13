# -*- coding: utf-8 -*-
"""test_protocol — myagentrag:// 解析校验 / 协议注册 / goto 与 play 别名执行（v0.1.2 统一入口）"""
import os
import subprocess

import pytest

import protocol


# ---------- parse_uri 校验矩阵（防注入；goto 必备 ws/entry，at 可选） ----------

def test_parse_uri_goto_valid():
    action, ws, entry, at = protocol.parse_uri(
        "myagentrag://goto?ws=%E6%88%91%E7%9A%84%E4%B9%A6%E6%9E%B6"
        "&entry=0123456789abcdef&at=624")
    assert action == "goto" and ws == "我的书架"
    assert entry == "0123456789abcdef" and at == 624


def test_parse_uri_goto_at_optional():
    """at 可选：媒体缺省从头（None 由 ws_goto 落为 0），文档条目上被忽略"""
    action, ws, entry, at = protocol.parse_uri(
        "myagentrag://goto?ws=lib&entry=0123456789abcdef")
    assert action == "goto" and at is None


def test_parse_uri_goto_decimal_at_rounds_to_second():
    _, _, _, at = protocol.parse_uri(
        "myagentrag://goto?ws=lib&entry=0123456789abcdef&at=624.34")
    assert at == 624


def test_parse_uri_play_alias_valid():
    """v0.1.1 链接兼容别名：play 等价于带 at 的 goto"""
    action, ws, entry, at = protocol.parse_uri(
        "myagentrag://play?ws=lib&entry=0123456789abcdef&at=63")
    assert action == "play" and (ws, entry, at) == ("lib", "0123456789abcdef", 63)


@pytest.mark.parametrize("uri", [
    "http://goto?ws=lib&entry=0123456789abcdef&at=1",                # 未知 scheme
    "myagentrag://open?ws=lib&entry=0123456789abcdef",               # 未知 action（统一入口是 goto）
    "myagentrag://goto?ws=lib&entry=0123456789abcdef&x=1",           # 多余参数
    "myagentrag://goto?ws=lib&entry=0123456789abcdef&path=C:/Windows/System32/calc.exe",  # 路径注入（安全红线）
    "myagentrag://goto?entry=0123456789abcdef",                      # 缺 ws
    "myagentrag://goto?ws=lib",                                      # 缺 entry
    "myagentrag://goto?ws=lib&entry=../../etc/passwd",               # 路径穿越
    "myagentrag://goto?ws=lib&entry=0123456789abcde",                # entry 长度错
    "myagentrag://goto?ws=lib&entry=zzzzzzzzzzzzzzzz",               # entry 非 hex
    "myagentrag://goto?ws=lib&entry=0123456789abcdef&at=$-rm",       # at 注入
    "myagentrag://goto?ws=lib&entry=0123456789abcdef&at=-5",         # at 负值
    "myagentrag://goto?ws=lib&entry=0123456789abcdef&at=999999",     # at 超 24h
    "myagentrag://goto?ws=lib&entry=0123456789abcdef&at=abc",        # at 非数值
    "myagentrag://goto?ws=bad/name&entry=0123456789abcdef",          # 库名非法字符
    "myagentrag://goto?ws=&entry=0123456789abcdef",                  # 空库名
    "myagentrag://play?ws=lib&entry=0123456789abcdef",               # 别名仍要求 at
    "myagentrag://play?ws=lib&entry=0123456789abcdef&at=1&x=1",      # 别名多余参数
])
def test_parse_uri_rejects(uri):
    with pytest.raises(ValueError):
        protocol.parse_uri(uri)


# ---------- handle_uri ----------

def test_handle_uri_invalid_structured():
    r = protocol.handle_uri("myagentrag://goto?ws=lib&entry=bad")
    assert r["success"] is False and "error_i18n" in r and "不合法" in r["error"]


def test_handle_uri_goto_reuses_ws_goto(monkeypatch):
    """goto → workspace.ws_goto（媒体播放 / 文档打开的分派在 workspace 内）"""
    import workspace
    captured = {}

    def fake_goto(ws, entry, at=None):
        captured.update(ws=ws, entry=entry, at=at)
        return {"success": True}

    monkeypatch.setattr(workspace, "ws_goto", fake_goto)
    r = protocol.handle_uri("myagentrag://goto?ws=lib&entry=0123456789abcdef")
    assert r["success"] is True and captured == {
        "ws": "lib", "entry": "0123456789abcdef", "at": None}
    r2 = protocol.handle_uri(
        "myagentrag://goto?ws=lib&entry=0123456789abcdef&at=63")
    assert r2["success"] is True and captured["at"] == 63


def test_handle_uri_play_alias_reuses_ws_play(monkeypatch):
    """play 别名（v0.1.1 链接）仍走 ws_play，参数语义不变"""
    import workspace
    captured = {}

    def fake_play(ws, entry, at, **kw):
        captured.update(ws=ws, entry=entry, at=at)
        return {"success": True}

    monkeypatch.setattr(workspace, "ws_play", fake_play)
    r = protocol.handle_uri(
        "myagentrag://play?ws=lib&entry=0123456789abcdef&at=63")
    assert r["success"] is True
    assert captured == {"ws": "lib", "entry": "0123456789abcdef", "at": "63"}


# ---------- 协议注册（Windows 真实注册表往返，隔离测试子键） ----------

@pytest.mark.skipif(os.name != "nt", reason="Windows 注册表路径")
def test_protocol_register_unregister_windows(monkeypatch):
    from pathlib import Path
    test_key = r"HKCU\Software\Classes\myagentrag-pytest"
    monkeypatch.setattr(protocol, "_REG_KEY", test_key)
    subprocess.run(["reg", "delete", test_key, "/f"], capture_output=True)
    try:
        r = protocol.register_protocol()
        assert r["success"] and r["registered"] is True
        assert "play.py" in r["handler"]            # 只绑定受管目录内的自定位启动器
        assert "extract.py" not in r["handler"]     # 注册项不写死源码/技能目录
        launcher = Path(r["launcher"])
        assert launcher.is_file()
        assert "--goto-uri" in launcher.read_text(encoding="utf-8")   # 转发统一入口
        assert (launcher.parent / "skill.json").is_file()
        assert r["skill_dir"] and r["hint"]
        d = subprocess.run(["reg", "query", test_key],
                           capture_output=True, text=True, errors="replace")
        assert d.returncode == 0 and "URL:myagentrag goto" in d.stdout
        q = subprocess.run(["reg", "query", test_key + r"\shell\open\command"],
                           capture_output=True, text=True, errors="replace")
        assert q.returncode == 0 and "play.py" in q.stdout
        u = protocol.unregister_protocol()
        assert u["success"] and u["unregistered"] is True
        q2 = subprocess.run(["reg", "query", test_key], capture_output=True, text=True, errors="replace")
        assert q2.returncode != 0                   # 自有命名空间已完全移除
        assert not launcher.parent.exists()         # 启动器与记录一并移除
    finally:
        subprocess.run(["reg", "delete", test_key, "/f"], capture_output=True)


def test_launcher_env_and_config_resolution(tmp_path):
    """自定位启动器：MYAGENTRAG_SKILL_DIR 环境变量优先 → skill.json 回退 →
    均不可用时报错退出（移动技能目录不写死失效的关键路径）"""
    import json
    import sys
    launcher, _ = protocol._write_launcher()
    skill = tmp_path / "fakeskill"
    (skill / "scripts").mkdir(parents=True)
    out = tmp_path / "argv.txt"
    (skill / "scripts" / "extract.py").write_text(
        "import sys, pathlib;"
        "pathlib.Path(r'%s').write_text(' '.join(sys.argv[1:]), encoding='utf-8')" % out,
        encoding="utf-8")
    cfg = launcher.parent / "skill.json"
    # 1) 环境变量优先（即使 skill.json 指向别处）
    cfg.write_text(json.dumps({"skill_dir": "C:/nonexistent"}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(launcher), "myagentrag://play?a=1"],
                       env={**os.environ, "MYAGENTRAG_SKILL_DIR": str(skill)},
                       capture_output=True, text=True, errors="replace")
    argv = out.read_text(encoding="utf-8")
    assert r.returncode == 0 and "a=1" in argv and "--goto-uri" in argv
    # 2) 环境变量缺失 → skill.json 回退
    out.unlink()
    cfg.write_text(json.dumps({"skill_dir": str(skill)}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "MYAGENTRAG_SKILL_DIR"}
    r = subprocess.run([sys.executable, str(launcher), "myagentrag://play?b=2"],
                       env=env, capture_output=True, text=True, errors="replace")
    assert r.returncode == 0 and "b=2" in out.read_text(encoding="utf-8")    # 3) 均不可用 → 明确指引 + rc=1（不静默失败）
    cfg.write_text("{broken", encoding="utf-8")
    r = subprocess.run([sys.executable, str(launcher), "u"], env=env,
                       capture_output=True, text=True, errors="replace")
    assert r.returncode == 1 and "MYAGENTRAG_SKILL_DIR" in r.stderr
