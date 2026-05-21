"""KnowledgeBase 单测（绕过真实 embedding API）。"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

pytest.importorskip("sqlite_vec")

from trpg2novel.rag.config import KBConfig
from trpg2novel.rag.store import KnowledgeBase, _pack_vec


def _stub_embed(monkeypatch, vectors_for_texts):
    """让 embed_texts 返回预设向量；用 vectors_for_texts(text) 决定单条向量。"""
    def fake_embed(texts, *, api_key, base_url, model, batch_size=64):
        return [vectors_for_texts(t) for t in texts]

    monkeypatch.setattr("trpg2novel.rag.store.embed_texts", fake_embed)


def test_pack_vec_roundtrip():
    v = [0.1, 0.2, -0.3, 0.4]
    b = _pack_vec(v)
    unpacked = list(struct.unpack(f"{len(v)}f", b))
    assert all(abs(a - c) < 1e-6 for a, c in zip(v, unpacked))


def test_rebuild_and_query(tmp_path, monkeypatch):
    kb_dir = tmp_path / "knowledge_base"
    cfg = KBConfig(api_key="fake", dim=4, chunk_size=100, chunk_overlap=0, min_score=0.0)
    kb = KnowledgeBase.open(kb_dir, cfg)

    # 写两个 source
    (kb.sources_dir / "starborn.md").write_text(
        "星界精灵来自星光位面，与费伦凡人有别。", encoding="utf-8"
    )
    (kb.sources_dir / "geography.md").write_text(
        "费伦的剑湾地区有许多繁荣城市。", encoding="utf-8"
    )

    # 让两条文本拿到正交向量
    def vec_for(text):
        if "星界" in text:
            return [1.0, 0.0, 0.0, 0.0]
        if "费伦" in text or "剑湾" in text:
            return [0.0, 1.0, 0.0, 0.0]
        # 查询：偏向星界
        return [0.9, 0.1, 0.0, 0.0]

    _stub_embed(monkeypatch, vec_for)

    res = kb.rebuild_from_sources()
    assert res["sources"] == 2
    assert res["chunks"] == 2
    assert kb.count_chunks() == 2

    hits = kb.query("讲讲星界精灵", top_k=2)
    assert len(hits) >= 1
    # 第一名应是星界相关
    assert "星界" in hits[0].text
    assert hits[0].source == "starborn.md"
    assert hits[0].distance < hits[-1].distance  # 已排序


def test_empty_kb_returns_empty(tmp_path, monkeypatch):
    cfg = KBConfig(api_key="fake", dim=4)
    kb = KnowledgeBase.open(tmp_path / "kb", cfg)
    _stub_embed(monkeypatch, lambda t: [0.0, 0.0, 0.0, 0.0])
    assert kb.query("任意 query", top_k=3) == []


def test_query_without_api_key(tmp_path):
    cfg = KBConfig(api_key="", dim=4)
    kb = KnowledgeBase.open(tmp_path / "kb", cfg)
    assert kb.query("test") == []


def test_dim_auto_update(tmp_path, monkeypatch):
    """embedding 实际维度与 cfg.dim 不符时，store 应自动重建 vec 表。"""
    cfg = KBConfig(api_key="fake", dim=4, chunk_size=100, chunk_overlap=0, min_score=0.0)
    kb = KnowledgeBase.open(tmp_path / "kb", cfg)
    (kb.sources_dir / "a.md").write_text("内容", encoding="utf-8")

    # 返回 8 维向量（与 cfg.dim=4 不符）
    monkeypatch.setattr(
        "trpg2novel.rag.store.embed_texts",
        lambda texts, **kw: [[0.1] * 8 for _ in texts],
    )
    res = kb.rebuild_from_sources()
    assert res["chunks"] == 1
    assert kb.cfg.dim == 8
