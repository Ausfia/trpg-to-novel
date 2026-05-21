"""RAG — 世界观知识库（检索式注入）。

子模块：
- config: KBConfig 数据类 + 读写 kb_config.yaml
- chunker: 段落优先的文本切分
- embedder: OpenAI 兼容的 embedding 客户端
- store: sqlite-vec 向量库封装
"""

from trpg2novel.rag.chunker import split_text
from trpg2novel.rag.config import KBConfig, load_kb_config, save_kb_config
from trpg2novel.rag.embedder import embed_texts
from trpg2novel.rag.store import KnowledgeBase, RetrievedChunk

__all__ = [
    "KBConfig",
    "KnowledgeBase",
    "RetrievedChunk",
    "embed_texts",
    "load_kb_config",
    "save_kb_config",
    "split_text",
]
