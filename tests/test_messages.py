# -*- coding: utf-8 -*-
"""test_messages — 双语反馈模块"""
import pytest

import messages


def test_set_get_lang():
    messages.set_lang("en")
    assert messages.get_lang() == "en"
    messages.set_lang("zh-CN")
    assert messages.get_lang() == "zh"
    messages.set_lang("fr")
    assert messages.get_lang() == "zh"  # 不支持的语言回落 zh


def test_msg_zh_en_and_params():
    messages.set_lang("zh")
    assert messages.msg("file_not_found", path="X") == "文件不存在: X"
    messages.set_lang("en")
    assert messages.msg("file_not_found", path="X") == "File not found: X"


def test_msg_pair_and_err_result():
    pair = messages.msg_pair("no_input")
    assert pair == {"zh": "错误：请提供 --url 或 --file", "en": "Error: --url or --file is required"}
    messages.set_lang("en")
    er = messages.err_result("unsupported_type", t="foo")
    assert er == {"success": False, "error": "Unsupported content type: foo",
                  "error_i18n": {"zh": "不支持的内容类型: foo", "en": "Unsupported content type: foo"}}
    cur, p = messages.err_field("cannot_extract", ext=".md")
    assert cur == "Cannot extract .md file content" and p["zh"].endswith(".md 文件内容")


def test_unknown_key_passthrough():
    assert messages.msg("definitely_not_a_key") == "definitely_not_a_key"
    assert messages.msg_pair("definitely_not_a_key") == {"zh": "definitely_not_a_key",
                                                         "en": "definitely_not_a_key"}


def test_detect_env_override(monkeypatch):
    monkeypatch.setenv("SMART_SUMMARIZE_LANG", "en-US")
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    assert messages.detect_lang() == "en"
    monkeypatch.setenv("SMART_SUMMARIZE_LANG", "zh_CN")
    assert messages.detect_lang() == "zh"
    monkeypatch.delenv("SMART_SUMMARIZE_LANG")
    # 未设置时应有确定值（zh 或 en，取决于宿主系统）
    assert messages.detect_lang() in ("zh", "en")


def test_init_explicit_and_auto():
    assert messages.init("en") == "en"
    assert messages.init(None) in ("zh", "en")
    assert messages.init("zh") == "zh"


def test_every_entry_has_zh_and_en():
    for key, entry in messages.MESSAGES.items():
        assert set(entry.keys()) == {"zh", "en"}, key
        assert entry["zh"].strip() and entry["en"].strip(), key


def test_warn_goes_to_stderr(capsys):
    messages.set_lang("zh")
    messages.warn("warn_no_lib", lib="openpyxl")
    err = capsys.readouterr().err
    assert "未安装 openpyxl" in err
