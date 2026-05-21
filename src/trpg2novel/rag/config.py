"""KBConfig — 知识库配置 (kb_config.yaml) 读写。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class KBConfig:
    # embedding 服务
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "text-embedding-3-small"
    dim: int = 1536  # 默认 OpenAI text-embedding-3-small 维度
    # 切分
    chunk_size: int = 400
    chunk_overlap: int = 80
    # 检索
    top_k: int = 5
    min_score: float = 0.3   # 越大越严；store 内部用距离，会换算

    def is_configured(self) -> bool:
        return bool(self.api_key.strip())


def load_kb_config(path: Path) -> KBConfig:
    if not path.exists():
        return KBConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    emb = raw.get("embedding", {}) or {}
    chunk = raw.get("chunk", {}) or {}
    retrieval = raw.get("retrieval", {}) or {}
    return KBConfig(
        api_key=emb.get("api_key", ""),
        base_url=emb.get("base_url", "https://api.openai.com/v1"),
        model=emb.get("model", "text-embedding-3-small"),
        dim=int(emb.get("dim", 1536)),
        chunk_size=int(chunk.get("size", 400)),
        chunk_overlap=int(chunk.get("overlap", 80)),
        top_k=int(retrieval.get("top_k", 5)),
        min_score=float(retrieval.get("min_score", 0.3)),
    )


def save_kb_config(cfg: KBConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "embedding": {
            "api_key": cfg.api_key,
            "base_url": cfg.base_url,
            "model": cfg.model,
            "dim": cfg.dim,
        },
        "chunk": {"size": cfg.chunk_size, "overlap": cfg.chunk_overlap},
        "retrieval": {"top_k": cfg.top_k, "min_score": cfg.min_score},
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
