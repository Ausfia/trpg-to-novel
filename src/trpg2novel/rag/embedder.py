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
    progress_cb = None,
) -> list[list[float]]:
    if not texts:
        return []
    client = make_client(api_key, base_url)
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        out.extend([item.embedding for item in resp.data])
        if progress_cb:
            done = min(i + batch_size, len(texts))
            progress_cb("embed", done, len(texts))
    return out
