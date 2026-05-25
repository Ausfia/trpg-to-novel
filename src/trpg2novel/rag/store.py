"""KnowledgeBase — sqlite-vec 封装：建库、入库、检索。"""

from __future__ import annotations

import sqlite3
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import sqlite_vec

from trpg2novel.rag.chunker import split_text
from trpg2novel.rag.config import KBConfig


@dataclass
class RetrievedChunk:
    source: str          # 来源文件名（如 "races_starborn.md"）
    text: str            # 片段内容
    distance: float      # sqlite-vec 距离（越小越相关）

    @property
    def score(self) -> float:
        """把距离转成 0-1 相似度（粗略：1 / (1 + d)）。"""
        return 1.0 / (1.0 + self.distance)


def _pack_vec(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _read_source_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp936", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


class KnowledgeBase:
    """单个 campaign 的世界观知识库。"""

    def __init__(self, db_path: Path, sources_dir: Path, cfg: KBConfig):
        self.db_path = db_path
        self.sources_dir = sources_dir
        self.cfg = cfg
        # 每个线程独立的连接，彻底避免 SQLite 跨线程并发问题
        self._local = threading.local()

    @classmethod
    def open(cls, kb_dir: Path, cfg: KBConfig) -> "KnowledgeBase":
        kb_dir.mkdir(parents=True, exist_ok=True)
        sources = kb_dir / "sources"
        sources.mkdir(exist_ok=True)
        kb = cls(kb_dir / "kb.sqlite", sources, cfg)
        kb._ensure_schema()
        return kb

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _ensure_schema(self) -> None:
        conn = self._connect()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS chunks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                ord INTEGER NOT NULL,
                text TEXT NOT NULL
            )"""
        )
        # 检查 vec 表的维度是否匹配；不匹配则重建
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vec_chunks'")
        if cur.fetchone():
            # 维度变化时清表
            try:
                info = conn.execute("SELECT * FROM vec_chunks LIMIT 0").description
                # 通过 pragma 拿不到 vec0 的维度；保险起见用 try insert 校验在 rebuild 时做
            except sqlite3.OperationalError:
                pass
        else:
            conn.execute(
                f"CREATE VIRTUAL TABLE vec_chunks USING vec0(embedding float[{self.cfg.dim}])"
            )
        conn.commit()

    def reset(self) -> None:
        """清空所有 chunk 与向量。"""
        conn = self._connect()
        # DROP + CREATE 远快于 DELETE（百万级行 DELETE 会锁库很久）
        conn.execute("DROP TABLE IF EXISTS chunks")
        conn.execute("DROP TABLE IF EXISTS vec_chunks")
        conn.execute(
            """CREATE TABLE chunks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                ord INTEGER NOT NULL,
                text TEXT NOT NULL
            )"""
        )
        conn.execute(
            f"CREATE VIRTUAL TABLE vec_chunks USING vec0(embedding float[{self.cfg.dim}])"
        )
        conn.commit()

    def list_sources(self) -> list[Path]:
        return sorted(
            list(self.sources_dir.glob("*.md")) + list(self.sources_dir.glob("*.txt"))
        )

    def count_chunks(self) -> int:
        conn = self._connect()
        row = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return int(row[0]) if row else 0

    def rebuild_from_sources(self, progress_cb=None) -> dict:
        """扫 sources/ 下所有 .md/.txt，重建索引。

        Args:
            progress_cb: 可选回调 (stage, current, total) → None。
                stage 为 "chunk" / "embed" / "insert"。

        Returns:
            {"sources": int, "chunks": int}
        """
        if not self.cfg.is_configured():
            raise RuntimeError("KBConfig 未配置 api_key，无法重建索引。")

        try:
            self.reset()
            conn = self._connect()

            all_chunks: list[tuple[str, int, str]] = []  # (source, ord, text)
            sources = self.list_sources()
            for src in sources:
                text = _read_source_text(src)
                pieces = split_text(text, size=self.cfg.chunk_size, overlap=self.cfg.chunk_overlap)
                for i, p in enumerate(pieces):
                    all_chunks.append((src.name, i, p))

            if not all_chunks:
                return {"sources": len(sources), "chunks": 0}

            if progress_cb:
                progress_cb("chunk", len(all_chunks), len(all_chunks))

            # 嵌入
            from trpg2novel.rag.embedder import embed_texts

            texts = [c[2] for c in all_chunks]
            if progress_cb:
                progress_cb("embed", 0, len(texts))
            vectors = embed_texts(
                texts,
                api_key=self.cfg.api_key,
                base_url=self.cfg.base_url,
                model=self.cfg.model,
                progress_cb=progress_cb,
            )
            if progress_cb:
                progress_cb("embed", len(texts), len(texts))

            if vectors and len(vectors[0]) != self.cfg.dim:
                # 自动更新维度并重建 vec 表
                self.cfg.dim = len(vectors[0])
                conn.execute("DROP TABLE vec_chunks")
                conn.execute(
                    f"CREATE VIRTUAL TABLE vec_chunks USING vec0(embedding float[{self.cfg.dim}])"
                )

            # 入库
            for i, ((source, ord_, text), vec) in enumerate(zip(all_chunks, vectors), start=1):
                cur = conn.execute(
                    "INSERT INTO chunks(source, ord, text) VALUES (?, ?, ?)",
                    (source, ord_, text),
                )
                chunk_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                    (chunk_id, _pack_vec(vec)),
                )
                if progress_cb and i % 10 == 0:
                    progress_cb("insert", i, len(all_chunks))
            conn.commit()

            if progress_cb:
                progress_cb("insert", len(all_chunks), len(all_chunks))
            return {"sources": len(sources), "chunks": len(all_chunks)}
        finally:
            # worker 线程结束前必须显式关掉自己的连接，否则 WAL 写锁会残留，
            # 主线程后续 DELETE/DROP 会触发 "database is locked"。
            self.close()

    def query(self, query_text: str, top_k: int | None = None) -> list[RetrievedChunk]:
        """检索 top-K 相关片段。"""
        if not self.cfg.is_configured() or not query_text.strip():
            return []
        k = top_k or self.cfg.top_k
        conn = self._connect()
        if self.count_chunks() == 0:
            return []

        from trpg2novel.rag.embedder import embed_texts

        vec = embed_texts(
            [query_text],
            api_key=self.cfg.api_key,
            base_url=self.cfg.base_url,
            model=self.cfg.model,
        )[0]

        rows = conn.execute(
            """SELECT c.source, c.text, v.distance
               FROM vec_chunks v
               JOIN chunks c ON c.id = v.rowid
               WHERE v.embedding MATCH ? AND k = ?
               ORDER BY v.distance""",
            (_pack_vec(vec), k),
        ).fetchall()
        out = [RetrievedChunk(source=r[0], text=r[1], distance=float(r[2])) for r in rows]
        if self.cfg.min_score > 0:
            out = [r for r in out if r.score >= self.cfg.min_score]
        return out
