# -*- coding: utf-8 -*-
"""test_embeddings — 窗口切分 / 归一化 / KNN / 假嵌入确定性（离线）"""
import embeddings


def test_split_windows_short_text_single():
    t = "短文本，不分窗口。"
    w = embeddings.split_windows(t)
    assert w == [(0, len(t), t)]


def test_split_windows_offsets_are_exact_substrings():
    text = "第一句。" + "内容填充。" * 400  # 超过 800 字符
    windows = embeddings.split_windows(text, max_chars=800, overlap=100)
    assert len(windows) > 1
    for (s, e, wtext) in windows:
        assert text[s:e] == wtext  # 精确子串（偏移可信）
    # 相邻窗口有重叠
    for (s1, e1, _), (s2, _, _) in zip(windows, windows[1:]):
        assert s2 < e1
    # 尾部覆盖到文本末尾
    assert windows[-1][1] == len(text)


def test_split_windows_sentence_boundary():
    text = ("句子内容。" * 100) + "结尾句。"
    windows = embeddings.split_windows(text, max_chars=300, overlap=50)
    # 窗口切点应落在句号之后（不以句中"容"截断开头错位——检查窗口文本以句号结尾或为末尾窗口）
    for (s, e, wtext) in windows[:-1]:
        assert wtext.endswith("。") or wtext.endswith("？") or wtext.endswith("！")


def test_normalize_l2():
    v = embeddings.normalize([[3.0, 4.0]])
    assert abs(v[0][0] - 0.6) < 1e-6 and abs(v[0][1] - 0.8) < 1e-6


def test_knn_top_ordering():
    q = [1.0, 0.0]
    vecs = [[0.0, 1.0], [1.0, 0.0], [0.7071, 0.7071]]
    top = embeddings.knn_top(q, vecs, k=3)
    assert top[0] == (1, 1.0) or abs(top[0][1] - 1.0) < 1e-6
    assert top[0][0] == 1  # 与 q 完全一致的向量排第一
    assert top[1][0] == 2  # 45 度第二
    assert top[2][0] == 0  # 正交最末


def test_fake_embed_deterministic_and_normalized():
    a1 = embeddings.fake_embed(["你好世界"])
    a2 = embeddings.fake_embed(["你好世界"])
    assert a1 == a2  # 确定性
    n = sum(x * x for x in a1[0]) ** 0.5
    assert abs(n - 1.0) < 1e-5  # 已归一化
    b = embeddings.fake_embed(["完全不同的另一段文本内容"])
    assert a1[0] != b[0]


def test_local_opener_bypasses_proxy(monkeypatch):
    """回归（2026-09-09）：环境 HTTP_PROXY 指向代理且 NO_PROXY 残缺（如含全角逗号，
    127.0.0.1 不被绕过）时，回环请求若走默认 opener 会被代理 503，嵌入链路整体超时。
    _LOCAL_OPENER 显式绕过代理，该行为不得回退。"""
    import http.server
    import threading

    class _HealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    # 敌意代理环境：HTTP_PROXY 指向死端口，且无 NO_PROXY 兜底
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)

    srv = http.server.HTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with embeddings._LOCAL_OPENER.open(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            assert r.status == 200
    finally:
        srv.shutdown()


def test_query_embedding_cache(monkeypatch):
    """查询嵌入持久化缓存：同模型同文本第二次命中缓存（零嵌入调用）；
    缓存故障静默回退不影响正确性。"""
    import deps
    calls = {"n": 0}
    real = embeddings.embed_texts

    def counting(texts, model_id=None):
        calls["n"] += 1
        return real(texts, model_id=model_id)

    monkeypatch.setattr(embeddings, "embed_texts", counting)
    fake_file = "fake-qwen3.gguf"
    monkeypatch.setattr(deps, "_find_llama_embed", lambda: fake_file)
    monkeypatch.setattr(deps, "_find_model_file_embedding", lambda mid: fake_file)

    v1 = embeddings.cached_query_embedding("缓存命中测试句子甲", model_id="Qwen3-Embedding-0.6B")
    v2 = embeddings.cached_query_embedding("缓存命中测试句子甲", model_id="Qwen3-Embedding-0.6B")
    # 缓存存 float32（KNN 实际消费精度），回读与首算允许 1e-6 级浮点差
    assert all(abs(a - b) < 1e-6 for a, b in zip(v1, v2)) and len(v1) == len(v2)
    assert calls["n"] == 1
    embeddings.cached_query_embedding("另一个不同句子乙", model_id="Qwen3-Embedding-0.6B")
    assert calls["n"] == 2
