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
