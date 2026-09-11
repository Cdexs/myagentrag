# -*- coding: utf-8 -*-
"""test_slicing — 分片协议回归"""
import hashlib

import slicing
from slicing import make_chunks, make_tmpdir, write_slices, CHUNK_CHARS, CHUNK_OVERLAP_CHARS


def _long_text(paras=40):
    return "\n\n".join(f"第{i}段。" + "内容填充" * 30 for i in range(paras))


def test_make_chunks_paragraph_boundaries_and_overlap():
    text = "\n\n".join(f"段落{i}：" + "x" * 500 for i in range(200))
    chunks = make_chunks(text)
    assert len(chunks) > 1
    for a, b in zip(chunks, chunks[1:]):
        # 相邻片有重叠窗口
        assert b[:50] in a or b[:CHUNK_OVERLAP_CHARS].strip()[:20] in a
    assert all(len(c) <= CHUNK_CHARS + CHUNK_OVERLAP_CHARS + 2 for c in chunks)


def test_make_chunks_deterministic():
    t = _long_text()
    assert make_chunks(t) == make_chunks(t)


def test_hard_split_long_paragraph():
    text = "句子。" * 3000  # 单段超长
    chunks = make_chunks(text)
    assert all(len(c) <= CHUNK_CHARS + CHUNK_OVERLAP_CHARS + 2 for c in chunks)


def test_write_slices_manifest(tmp_path):
    text = _long_text(120)
    m = write_slices("标题", "src.md", text, temp_base_dir=tmp_path)
    assert m["platform"] == "slices"
    assert m["total_chunks"] == len(m["chunks"])
    assert m["total_chars"] == len(text)
    # 分片文件与校验和
    import pathlib
    chunk_dir = pathlib.Path(m["chunk_dir"])
    assert (chunk_dir / "manifest.json").exists()
    for entry in m["chunks"]:
        data = (chunk_dir / entry["file"]).read_text(encoding="utf-8")
        assert hashlib.sha256(data.encode("utf-8")).hexdigest()[:16] == entry["sha256"]


def test_write_slices_pick_chunk(tmp_path):
    text = _long_text(1200)
    one = write_slices("T", "s", text, args_slice=2, temp_base_dir=tmp_path)
    assert one["success"] is True and one["chunk"] == 2 and one["content"]
    full = write_slices("T", "s", text, temp_base_dir=tmp_path)
    assert full["total_chunks"] > 1 and one["content"] == _read_chunk(full, 2)
    bad = write_slices("T", "s", text, args_slice=999, temp_base_dir=tmp_path)
    assert bad["success"] is False and "片号" in bad["error"]


def _read_chunk(manifest, n):
    import pathlib
    chunk_dir = pathlib.Path(manifest["chunk_dir"])
    return (chunk_dir / f"chunk-{n:03d}.md").read_text(encoding="utf-8")


def test_make_tmpdir_under_base_and_sweep(tmp_path, monkeypatch):
    monkeypatch.setattr(slicing, "TEMP_BASE_DIR", tmp_path)
    stale = tmp_path / "myag_stale_x"
    stale.mkdir()
    import os
    old = 0 - (80 * 3600)  # 80 小时前
    os.utime(stale, (old, old))
    d = make_tmpdir("myag_new_")
    assert d.exists() and d.name.startswith("myag_new_")
    assert d.parent == tmp_path
    assert not stale.exists()  # 超期目录被清扫
    fresh = tmp_path / "myag_fresh"
    fresh.mkdir()
    make_tmpdir("myag_new2_")
    assert fresh.exists()  # 新目录保留


def test_temp_base_dir_default_managed_home(monkeypatch, tmp_path):
    """默认临时根 = 受管目录 tmp/（不再落系统临时目录，OPT 2026-09-11）"""
    monkeypatch.delenv("MYAGENTRAG_TMPDIR", raising=False)
    monkeypatch.setenv("MYAGENTRAG_HOME", str(tmp_path / "home"))
    base = slicing._default_temp_base_dir()
    assert base == tmp_path / "home" / "tmp"
    assert base.exists()


def test_temp_base_dir_env_override(monkeypatch, tmp_path):
    """显式 MYAGENTRAG_TMPDIR 仍优先（如指向其他磁盘）"""
    monkeypatch.setenv("MYAGENTRAG_TMPDIR", str(tmp_path / "custom"))
    assert slicing._default_temp_base_dir() == tmp_path / "custom"


def test_write_slices_sweeps_stale_dirs(tmp_path, monkeypatch):
    """P9：文档分片路径同样触发 72h 过期清扫（与 make_tmpdir 对称，不再只靠音视频路径顺带清理）"""
    import os
    import time
    monkeypatch.setattr(slicing, "TEMP_BASE_DIR", tmp_path)
    stale = tmp_path / "myag_slice_deadbeef"
    stale.mkdir()
    (stale / "chunk-001.md").write_text("x", encoding="utf-8")
    old = time.time() - 4 * 24 * 3600          # 4 天前 → 超过 72h
    os.utime(stale, (old, old))
    fresh = tmp_path / "myag_slice_fresh"
    fresh.mkdir()                              # 新目录不受清扫影响
    manifest = slicing.write_slices("t", "src.md", "x" * 100)
    assert not stale.exists()                  # 过期目录被清扫（P9 期望 ② = False）
    assert fresh.exists()
    assert manifest["total_chunks"] >= 1       # 分片功能本身正常


def test_write_slices_sweep_honors_explicit_base(tmp_path, monkeypatch):
    """P9：显式 temp_base_dir 参数时，清扫指向实际生效根目录"""
    import os
    import time
    monkeypatch.setattr(slicing, "TEMP_BASE_DIR", tmp_path / "global")
    (tmp_path / "global").mkdir()
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    stale = explicit / "myag_slice_old"
    stale.mkdir()
    old = time.time() - 4 * 24 * 3600
    os.utime(stale, (old, old))
    slicing.write_slices("t", "src.md", "y" * 100, temp_base_dir=explicit)
    assert not stale.exists()                  # 清扫的是 explicit 根，而非全局根
