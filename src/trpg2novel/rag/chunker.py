"""段落优先的中文文本切分。

规则：
- 优先按 `\n\n` 切段落；段落 ≤ size 时整段保留。
- 段落超长时按句号/问号/感叹号/分号断句二次切；仍超长再按 size 硬切。
- overlap 通过保留前一片段末尾若干字符实现。
"""

from __future__ import annotations

import re

_SENT_RE = re.compile(r"(?<=[。！？；!?\.;])")


def _split_long_paragraph(text: str, size: int) -> list[str]:
    """长段落按句号断；句子仍超长则硬切。"""
    sents = [s for s in _SENT_RE.split(text) if s.strip()]
    out: list[str] = []
    buf = ""
    for s in sents:
        if len(s) > size:
            # 句子本身超长，先把 buf 收尾，再硬切这个句子
            if buf:
                out.append(buf)
                buf = ""
            for i in range(0, len(s), size):
                out.append(s[i : i + size])
            continue
        if len(buf) + len(s) > size:
            out.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        out.append(buf)
    return out


def split_text(text: str, *, size: int = 400, overlap: int = 80) -> list[str]:
    """段落优先切分。

    Args:
        text: 原文。
        size: 单片最大字符数。
        overlap: 相邻片段重叠字符数（取上一片末尾）。

    Returns:
        片段列表（每片长度 ≤ size + overlap）。
    """
    if not text.strip():
        return []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if len(p) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_split_long_paragraph(p, size))
            continue
        candidate = (buf + "\n\n" + p) if buf else p
        if len(candidate) > size:
            chunks.append(buf)
            buf = p
        else:
            buf = candidate
    if buf:
        chunks.append(buf)

    if overlap <= 0 or len(chunks) <= 1:
        return chunks

    # 加 overlap：从第 2 片起，前缀粘上上一片末尾 overlap 字符
    out = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_tail = chunks[i - 1][-overlap:]
        out.append(prev_tail + chunks[i])
    return out
