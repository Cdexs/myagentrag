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


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch):
    """全局确定性假嵌入器：让所有入库/检索测试覆盖向量管线（不依赖真实模型/引擎）。
    需要测试真实失败路径时在用例内再行覆盖。"""
    import embeddings
    import deps
    fake_file = "fake-qwen3.gguf"
    monkeypatch.setattr(deps, "_find_llama_embed", lambda: fake_file)
    monkeypatch.setattr(deps, "_find_model_file_embedding", lambda mid: fake_file)
    monkeypatch.setattr(embeddings, "embed_texts",
                    lambda texts, model_id=None: embeddings.fake_embed(texts, dims=1024, model_id=model_id))
    yield


@pytest.fixture(autouse=True)
def _legacy_vec_backend(monkeypatch):
    """默认强制 legacy 向量后端（vectors 表 + numpy KNN，确定性）；
    vec0 路测试用 _vec0_backend 覆盖（见 test_workspace 的 vec0 用例）。"""
    import deps
    monkeypatch.setattr(deps, "sqlite_vec_ready",
                        lambda force=False: (False, {"error": "test-legacy"}))
    yield
