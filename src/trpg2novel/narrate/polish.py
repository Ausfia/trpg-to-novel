# -*- coding: utf-8 -*-
"""[8] 章节润色（Polish 阶段）。

对人工修订后的草稿（chXX_revised.md）进行 LLM 风格润色，
产出 chXX_polished.md。不增删情节，仅改善行文。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader

from trpg2novel.llm.client import chat, make_client
from trpg2novel.worldview import Worldview, load_worldview

if TYPE_CHECKING:
    from trpg2novel.rag.store import KnowledgeBase, RetrievedChunk

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_jenv = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


def polish_chapter(
    revised_text: str,
    worldview: Worldview | None = None,
    pc_facts: dict[str, list[str]] | None = None,
    last_chapter_summary: str = "",
    *,
    api_key: str,
    base_url: str,
    model: str,
    kb: "KnowledgeBase | None" = None,
    chapter_title: str = "",
) -> str:
    """对修订稿进行 LLM 润色，返回润色后的全文纯文本。"""
    if worldview is None:
        worldview = load_worldview("dnd5e")

    retrieved: list["RetrievedChunk"] = []
    if kb is not None:
        query = " ".join(filter(None, [chapter_title, revised_text[:300]]))
        try:
            retrieved = kb.query(query)
        except Exception:
            retrieved = []

    system_tmpl = _jenv.get_template("polish_system.j2")
    user_tmpl = _jenv.get_template("polish_user.j2")

    system_prompt = system_tmpl.render(
        worldview=worldview,
        pc_facts=pc_facts or {},
        last_chapter_summary=last_chapter_summary,
        retrieved=retrieved,
    )
    user_prompt = user_tmpl.render(revised_text=revised_text)

    client = make_client(api_key, base_url)
    return chat(client, model, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.7, max_tokens=8000)
