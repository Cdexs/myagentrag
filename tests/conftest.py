# -*- coding: utf-8 -*-
"""pytest 共享配置：脚本目录入 sys.path、语言固定、workspace 临时根目录"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import atexit
import os
import shutil
import tempfile

# 受管目录指向一次性临时 home：进程退出即删（测试产物不留在磁盘上）
def _cleanup_temp_dirs():
    """先释放进程级 DB 连接池（打开句柄会阻止 Windows 删除目录），再删临时目录。"""
    try:
        import workspace
        workspace.close_pooled_connections()
    except Exception:
        pass
    for _ in range(3):
        dirs = [d for d in _TEMP_DIRS if d and os.path.exists(d)]
        if not dirs:
            return
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
        if all(not os.path.exists(d) for d in dirs):
            return
        import time
        time.sleep(0.2)


_TEMP_DIRS = []

if "MYAGENTRAG_HOME" not in os.environ:
    _test_home = tempfile.mkdtemp(prefix="myag-test-home-")
    os.environ["MYAGENTRAG_HOME"] = _test_home
    _TEMP_DIRS.append(_test_home)
    atexit.register(_cleanup_temp_dirs)

# 切片临时目录也一律落在测试临时 home 内——测试绝不写用户受管 tmp
# （用户环境可能已设 MYAGENTRAG_TMPDIR，故此处直接覆盖；单个用例要测
#  环境变量链时用 monkeypatch.setenv 在其内部覆盖即可）
os.environ["MYAGENTRAG_TMPDIR"] = os.path.join(os.environ["MYAGENTRAG_HOME"], "tmp")

# pytest 自建临时根（tmp_path / tmp_path_factory）同样用完即删——
# 默认策略是"保留最近 N 次会话"，磁盘要等到下轮才释放，不符合即时回收要求
_SESSION_TMP = []


def pytest_configure(config):
    if not getattr(config.option, "basetemp", None):
        _SESSION_TMP.append(tempfile.mkdtemp(prefix="myag-pytest-"))
        _TEMP_DIRS.append(_SESSION_TMP[-1])
        config.option.basetemp = _SESSION_TMP[-1]


def pytest_unconfigure(config):
    _cleanup_temp_dirs()
    _SESSION_TMP.clear()


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
    monkeypatch.setenv("MYAGENTRAG_WORKSPACES_DIR", str(tmp_path / "workspaces"))
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
