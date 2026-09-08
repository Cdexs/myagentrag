# -*- coding: utf-8 -*-
"""向量嵌入模块（方案 docs/kb-sqlite-fts5-design-v1.5.md §8C）

默认模型 Qwen3-Embedding-0.6B（官方 GGUF Q8_0），推理引擎 llama.cpp（用户决策 2026-09-06）：
- 窗口切分：片内 ≤max_chars 字符窗口（中文 1 字 ≈ 1 token，800 字符保守对齐 1024 token 上限），
  相邻窗口重叠，句界优先切分；每窗口一个向量并记录 full.md 字符偏移（§8C.2）；
- 推理编排：按需拉起 llama-server（127.0.0.1 随机端口，/v1/embeddings OpenAI 兼容端点），
  批量嵌入后进程终止——技能一次性调用，不留常驻服务；
- 本模块只依赖标准库 + 惰性 numpy（专用运行时内必有 numpy；缺失时 KNN 走纯 Python 路径，
  保证模块在任何解释器可导入、可测试）。真实语义质量依赖 GGUF 模型，测试用确定性假模型。
"""
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import deps

DEFAULT_MODEL = "Qwen3-Embedding-0.6B"

# 窗口参数：800 字符 ≈ 中文 800 token（Qwen3/bge 词表对中文均约 1 字 1 token），
# 预留指令/特殊 token 余量后不超模型 1024 token 上限设计值
WINDOW_CHARS = 800
WINDOW_OVERLAP = 100
EMBED_BATCH = 16


# ==================== 窗口切分（字符偏移精确跟踪） ====================

def split_windows(text, max_chars=WINDOW_CHARS, overlap=WINDOW_OVERLAP):
    """文本 -> [(start, end, window_text)]；窗口为精确子串，句界优先切，相邻窗口重叠。"""
    if not text:
        return []
    if len(text) <= max_chars:
        return [(0, len(text), text)] if text.strip() else []
    out = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            # 窗口尾部回退到句界，避免切断句子
            window = text[start:end]
            best = max(window.rfind("。"), window.rfind("！"), window.rfind("？"),
                       window.rfind("."), window.rfind("!"), window.rfind("?"),
                       window.rfind("\n"))
            if best > max_chars // 2:
                end = start + best + 1
        seg = text[start:end]
        if seg.strip():
            out.append((start, end, seg))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return out


# ==================== llama-server 编排 ====================

def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_health(proc, port, timeout=180):
    """等待 llama-server 就绪（含模型加载）；进程提前退出即报错。"""
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("llama-server 进程提前退出（模型或参数问题）")
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("llama-server 健康检查超时")


def _post_embeddings(port, texts, timeout=300):
    body = json.dumps({"input": list(texts)}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/embeddings",
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    emb = sorted(data["data"], key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in emb]


def embed_texts(texts, model_id=DEFAULT_MODEL):
    """批量嵌入：拉起 llama-server -> 分批 POST /v1/embeddings -> L2 归一化 -> 关停。
    返回 list[list[float]]（与输入等长、等序）。组件缺失抛 RuntimeError。"""
    if not texts:
        return []
    engine = deps._find_llama_embed()
    gguf = deps._find_model_file_embedding(model_id)
    if not engine or not gguf:
        raise RuntimeError(f"嵌入组件未就绪（引擎={'缺' if not engine else '有'}，"
                           f"模型 {model_id}={'缺' if not gguf else '有'}）——请先完成依赖安装")
    port = _free_port()
    cmd = [str(engine), "-m", str(gguf), "--embedding", "--pooling", "last",
           "--host", "127.0.0.1", "--port", str(port), "--ctx-size", "2048"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_health(proc, port)
        out = []
        for i in range(0, len(texts), EMBED_BATCH):
            out.extend(_post_embeddings(port, texts[i:i + EMBED_BATCH]))
        return normalize(out)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


# ==================== 数值（惰性 numpy，纯 Python 兜底） ====================

def normalize(vectors):
    """L2 归一化；归一化后余弦相似度 = 点积。"""
    try:
        import numpy as np
        m = np.asarray(vectors, dtype="float32")
        if m.ndim == 1:
            m = m.reshape(1, -1)
        norm = np.linalg.norm(m, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return (m / norm).tolist()
    except ImportError:
        out = []
        for v in vectors:
            n = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / n for x in v])
        return out


def knn_top(query, vectors, k=20):
    """余弦 top-k（向量须已归一化，相似度 = 点积）。返回 [(index, score)] 降序。"""
    try:
        import numpy as np
        q = __import__("numpy").asarray(query, dtype="float32")
        m = np.asarray(vectors, dtype="float32")
        scores = m @ q
        order = __import__("numpy").argsort(-scores)[:k]
        return [(int(i), float(scores[i])) for i in order]
    except ImportError:
        scored = [(i, sum(a * b for a, b in zip(query, v))) for i, v in enumerate(vectors)]
        scored.sort(key=lambda x: -x[1])
        return scored[:k]


# ==================== 测试用确定性假模型 ====================

def fake_embed(texts, dims=16, model_id=None):
    """确定性假嵌入（测试专用）：文本哈希驱动，语义无关但可重复，
    用于在没有真实模型的环境验证窗口切分/入库/混合检索管线。"""
    out = []
    for t in texts:
        v, seed = [], t.encode("utf-8")
        for _ in range(dims):
            seed = hashlib.sha256(seed).digest()
            v.append(int.from_bytes(seed[:4], "little") / 2.0**32 - 0.5)
        out.append(v)
    return normalize(out)
