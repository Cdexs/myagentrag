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
        assert (launcher.parent / "skill.json").is_file()
        assert r["skill_dir"] and r["hint"]
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
    assert r.returncode == 0 and "a=1" in out.read_text(encoding="utf-8")
    # 2) 环境变量缺失 → skill.json 回退
    out.unlink()
    cfg.write_text(json.dumps({"skill_dir": str(skill)}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "MYAGENTRAG_SKILL_DIR"}
    r = subprocess.run([sys.executable, str(launcher), "myagentrag://play?b=2"],
                       env=env, capture_output=True, text=True, errors="replace")
    assert r.returncode == 0 and "b=2" in out.read_text(encoding="utf-8")
    # 3) 均不可用 → 明确指引 + rc=1（不静默失败）
    cfg.write_text("{broken", encoding="utf-8")
    r = subprocess.run([sys.executable, str(launcher), "u"], env=env,
                       capture_output=True, text=True, errors="replace")
    assert r.returncode == 1 and "MYAGENTRAG_SKILL_DIR" in r.stderr
