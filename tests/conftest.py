# -*- coding: utf-8 -*-
"""pytest 共享配置：脚本目录入 sys.path、语言固定、workspace 临时根目录"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(autouse=True)
def _pin_lang():
    """固定 zh，避免断言依赖宿主机 locale"""
    import messages
    messages.set_lang("zh")
    yield
    messages.set_lang("zh")


@pytest.fixture
def ws_mod(tmp_path, monkeypatch):
    """workspace 模块 + 指向临时目录的 workspace 根"""
    monkeypatch.setenv("SMART_SUMMARIZE_WORKSPACES_DIR", str(tmp_path / "workspaces"))
    import workspace
    return workspace
