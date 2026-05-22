"""[5] Chapter Boundary Detector + [6] Draft 章节生成。

用法：
    from trpg2novel.narrate.chapter import detect_boundary, draft_chapter, ChapterResult
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from jinja2 import Environment, FileSystemLoader

from trpg2novel.llm.client import chat, chat_json, make_client
from trpg2novel.narrate.narrative_feed import NarrativeEntry, build_feed, feed_to_text
from trpg2novel.parse.classify import TaggedEvent
from trpg2novel.segment.scene import Scene
from trpg2novel.state.story_state import StoryState
from trpg2novel.worldview import Worldview, load_worldview

# 仅类型注解；运行时延迟导入避免 sqlite-vec 必装
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trpg2novel.rag.store import KnowledgeBase, RetrievedChunk

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


@dataclass
class DetectionResult:
    status: str  # enough_for_chapter / partial_arc / mid_action
    chapter_title_suggestion: str = ""
    end_scene_id: str = ""
    focus_characters: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class ChapterResult:
    chapter_title: str
    focus_characters: list[str]
    draft_text: str
    scene_ids: list[str]
    source_event_count: int


def detect_boundary(
    scenes: Sequence[Scene],
    events_by_id: dict[str, TaggedEvent],
    state: StoryState,
    last_chapter_summary: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    story_name: str = "巨龙僭政",
) -> DetectionResult:
    """[5] 判断累积场景是否足以成章。"""
    feed = build_feed(scenes, events_by_id)
    excerpt = feed_to_text(feed, include_roll_outcomes=False)[:1200]

    system_prompt = (_PROMPTS_DIR / "chapter_detect_system.txt").read_text(encoding="utf-8")
    user_tmpl = _env.get_template("chapter_detect_user.j2")
    user_prompt = user_tmpl.render(
        story_name=story_name,
        characters=state.characters,
        last_chapter_summary=last_chapter_summary,
        scenes=scenes,
        total_events=sum(s.event_count for s in scenes),
        narrative_excerpt=excerpt,
    )

    client = make_client(api_key, base_url)
    raw = chat_json(client, model, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])
    return DetectionResult(
        status=raw.get("status", "insufficient"),
        chapter_title_suggestion=raw.get("chapter_title_suggestion", ""),
        end_scene_id=raw.get("end_scene_id", ""),
        focus_characters=raw.get("focus_characters", []),
        reason=raw.get("reason", ""),
    )


def draft_chapter(
    scenes: Sequence[Scene],
    events_by_id: dict[str, TaggedEvent],
    state: StoryState,
    nofiyad_facts: list[str],
    chapter_title: str,
    focus_characters: list[str],
    last_chapter_summary: str = "",
    absent_players: list[str] | None = None,
    retired_characters: list[dict] | None = None,
    *,
    api_key: str,
    base_url: str,
    model: str,
    worldview: Worldview | None = None,
    pc_facts: dict[str, list[str]] | None = None,
    story_name: str = "巨龙僭政",
    kb: "KnowledgeBase | None" = None,
) -> ChapterResult:
    """[6] 生成章节草稿。

    Args:
        worldview: 世界观（系统模板 + custom_lore）。不传则用默认 dnd5e 模板。
        pc_facts: ``{pc_name: facts}``。若给出则优先使用，prompt 渲染多 PC facts；
            否则回退到 ``nofiyad_facts`` 仅诺菲雅一人。
        kb: 可选知识库。若提供，会用章节标题 + focus + 场景摘要做 query 取 top-K
            片段注入 prompt（替代/补充直接的 custom_lore 全文注入）。
    """
    feed = build_feed(scenes, events_by_id)
    narrative_text = feed_to_text(feed, include_roll_outcomes=True)

    if worldview is None:
        worldview = load_worldview("dnd5e")

    retrieved: list["RetrievedChunk"] = []
    if kb is not None:
        scene_summary = " ".join(s.summary for s in scenes if getattr(s, "summary", ""))[:500]
        query = " ".join(filter(None, [
            chapter_title,
            " ".join(focus_characters),
            scene_summary,
        ]))
        try:
            retrieved = kb.query(query)
        except Exception:
            retrieved = []

    system_tmpl = _env.get_template("chapter_draft_system.j2")
    user_tmpl = _env.get_template("chapter_draft_user.j2")

    system_prompt = system_tmpl.render(
        story_name=story_name,
        worldview=worldview,
        characters=state.characters,
        absent_players=absent_players or [],
        retired_characters=retired_characters or [],
        nofiyad_facts=nofiyad_facts,
        pc_facts=pc_facts or {},
        last_chapter_summary=last_chapter_summary,
        lore_unlocked=state.lore_unlocked,
        retrieved=retrieved,
    )
    user_prompt = user_tmpl.render(
        chapter_title=chapter_title,
        focus_characters=focus_characters,
        scenes=scenes,
        narrative_text=narrative_text,
    )

    client = make_client(api_key, base_url)
    draft_text = chat(client, model, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.85, max_tokens=6000)

    total_events = sum(s.event_count for s in scenes)
    return ChapterResult(
        chapter_title=chapter_title,
        focus_characters=focus_characters,
        draft_text=draft_text,
        scene_ids=[s.id for s in scenes],
        source_event_count=total_events,
    )


def save_chapter_draft(result: ChapterResult, out_path: Path) -> None:
    """保存章节草稿为 Markdown。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = f"# {result.chapter_title}\n\n"
    meta = (
        f"<!-- scenes: {', '.join(result.scene_ids)} | "
        f"events: {result.source_event_count} | "
        f"focus: {', '.join(result.focus_characters)} -->\n\n"
    )
    out_path.write_text(header + meta + result.draft_text, encoding="utf-8")
