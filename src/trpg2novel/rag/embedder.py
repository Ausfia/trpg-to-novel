"""OpenAI 兼容的 embedding 客户端。

复用 trpg2novel.llm.client 的 OpenAI SDK 实例，但调 embeddings.create。
"""

from __future__ import annotations

from trpg2novel.llm.client import make_client


def embed_texts(
    texts: list[str],
    *,
    api_key: str,
    base_url: str,
    model: str,
    batch_size: int = 64,
) -> list[list[float]]:
    """对 texts 批量取 embedding。

    Args:
        texts: 待嵌入文本列表。
        api_key / base_url / model: 由 KBConfig 提供。
        batch_size: 每次 API 调用的最大文本数。

    Returns:
        每条文本对应的浮点向量。顺序保持。
    """
    if not texts:
        return []
    client = make_client(api_key, base_url)
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        out.extend([item.embedding for item in resp.data])
    return out
