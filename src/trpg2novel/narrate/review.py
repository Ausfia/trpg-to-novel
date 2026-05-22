# -*- coding: utf-8 -*-
"""[9] 章节一致性检查（Review 阶段）。

对章节稿（draft 或 polished）进行 LLM 一致性审查：
- 角色外貌/行为/语气是否符合人物卡
- 地名/阵营/专有名词是否符合世界观知识库
- 与上一章结尾是否衔接
- 章节内部是否自相矛盾

公开接口：
    review_chapter(chapter_text, ...) -> ReviewResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader

from trpg2novel.llm.client import chat_json, make_client
from trpg2novel.worldview import Worldview, load_worldview

if TYPE_CHECKING:
    from trpg2novel.rag.store import KnowledgeBase, RetrievedChunk

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_jenv = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


@dataclass
class ReviewIssue:
    type: str
    severity: str
    location: str
    description: str
    suggestion: str


@dataclass
class ReviewResult:
    issues: list[ReviewIssue] = field(default_factory=list)
    summary: str = ""
    passed: bool = True

    @property
    def severe_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "严重")

    @property
    def normal_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "一般")

    @property
    def minor_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "轻微")


def review_chapter(
    chapter_text: str,
    worldview: Worldview | None = None,
    pc_facts: dict[str, list[str]] | None = None,
    last_chapter_summary: str = "",
    *,
    api_key: str,
    base_url: str,
    model: str,
    kb: "KnowledgeBase | None" = None,
    chapter_title: str = "",
) -> ReviewResult:
    """对章节稿进行 LLM 一致性审查，返回 ReviewResult。"""
    if worldview is None:
        worldview = load_worldview("dnd5e")

    retrieved: list["RetrievedChunk"] = []
    if kb is not None:
        query = " ".join(filter(None, [chapter_title, chapter_text[:300]]))
        try:
            retrieved = kb.query(query)
        except Exception:
            retrieved = []

    system_tmpl = _jenv.get_template("review_system.j2")
    user_tmpl = _jenv.get_template("review_user.j2")

    system_prompt = system_tmpl.render(
        worldview=worldview,
        pc_facts=pc_facts or {},
        last_chapter_summary=last_chapter_summary,
        retrieved=retrieved,
    )
    user_prompt = user_tmpl.render(chapter_text=chapter_text)

    client = make_client(api_key, base_url)
    raw = chat_json(client, model, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.3, max_tokens=3000)

    issues = []
    for item in raw.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append(ReviewIssue(
            type=str(item.get("type", "")),
            severity=str(item.get("severity", "轻微")),
            location=str(item.get("location", "")),
            description=str(item.get("description", "")),
            suggestion=str(item.get("suggestion", "")),
        ))

    return ReviewResult(
        issues=issues,
        summary=str(raw.get("summary", "")),
        passed=bool(raw.get("pass", len(issues) == 0)),
    )
