"""chunker 单测。"""

from trpg2novel.rag.chunker import split_text


def test_empty():
    assert split_text("") == []
    assert split_text("   \n\n  ") == []


def test_short_text_single_chunk():
    text = "短短的一段世界观描述。"
    assert split_text(text, size=400, overlap=80) == [text]


def test_paragraph_priority():
    paragraphs = ["第一段的内容。" * 10, "第二段的内容。" * 10, "第三段的内容。" * 10]
    text = "\n\n".join(paragraphs)
    chunks = split_text(text, size=100, overlap=0)
    assert len(chunks) >= 2
    # 段落分隔保留
    for c in chunks:
        assert c.strip()


def test_long_paragraph_sentence_split():
    text = "句子一。句子二。句子三。" * 30
    chunks = split_text(text, size=50, overlap=0)
    assert len(chunks) > 1
    for c in chunks:
        # 不要求严格 ≤ size，因为单句可能本身较长被硬切；但应远小于全文
        assert len(c) <= 100


def test_overlap_present():
    paragraphs = ["A" * 100, "B" * 100, "C" * 100]
    text = "\n\n".join(paragraphs)
    chunks = split_text(text, size=120, overlap=20)
    # 从第二片起应带上前一片末尾的 20 字符
    if len(chunks) > 1:
        assert chunks[1].startswith(chunks[0][-20:])


def test_no_overlap_when_zero():
    text = "A" * 50 + "\n\n" + "B" * 50
    chunks = split_text(text, size=60, overlap=0)
    if len(chunks) > 1:
        assert not chunks[1].startswith(chunks[0][-10:])


def test_hard_split_when_single_sentence_too_long():
    """没有句号的长字符串应被硬切。"""
    text = "x" * 1000
    chunks = split_text(text, size=200, overlap=0)
    assert len(chunks) >= 5
    assert all(len(c) <= 200 for c in chunks)
