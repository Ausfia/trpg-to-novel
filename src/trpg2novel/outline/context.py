"""Outline context — 组装 LLM 大纲生成所需的上下文。

把零散的 Campaign / Worldview / 人物卡 / 故事状态 / 场景叙事文本 /（可选）RAG
合并为统一字典，供 ``generate.py`` / ``propose.py`` / ``revise.py`` 各 prompt 渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from trpg2novel.character.card_loader import CharacterCard
from trpg2novel.narrate.narrative_feed import build_feed, feed_to_text
from trpg2novel.parse.classify import TaggedEvent
from trpg2novel.segment.scene import Scene
from trpg2novel.state.story_state import StoryState
from trpg2novel.worldview import Worldview


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class OutlineContext:
    """LLM 大纲 prompt 的上下文容器。"""

    worldview_excerpt: str = ""
    custom_lore: str = ""
    pc_facts: str = ""
    state_summary: str = ""
    lore_unlocked: list[str] = field(default_factory=list)
    narrative_excerpt: str = ""
    scene_summary_lines: list[str] = field(default_factory=list)
    last_volume_summary: str = ""
    rag_excerpt: str = ""

    def to_prompt_dict(self) -> dict:
        return {
            "worldview_excerpt": self.worldview_excerpt,
            "custom_lore": self.custom_lore,
            "pc_facts": self.pc_facts,
            "state_summary": self.state_summary,
            "lore_unlocked": list(self.lore_unlocked),
            "narrative_excerpt": self.narrative_excerpt,
            "scene_summary_lines": list(self.scene_summary_lines),
            "last_volume_summary": self.last_volume_summary,
            "rag_excerpt": self.rag_excerpt,
        }


# ---------------------------------------------------------------------------
# 单一字段构造器
# ---------------------------------------------------------------------------


def render_pc_facts(cards: dict[str, CharacterCard]) -> str:
    """把人物卡的 ``atomic_facts`` 拼成 prompt 友好的多行文本。"""
    lines: list[str] = []
    for name, card in cards.items():
        retired = ""
        if card.left_after_session:
            retired = f"（已退团于 {card.left_after_session}）"
        lines.append(f"## {name}{retired}")
        for fact in (card.atomic_facts or []):
            lines.append(f"- {fact}")
        if card.exit_story:
            lines.append(f"- 离场故事：{card.exit_story}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_state_summary(state: StoryState) -> str:
    """把 StoryState.characters / world / lore 摘要为短文本。"""
    parts: list[str] = []

    if state.characters:
        char_lines = []
        for name, cs in state.characters.items():
            extras = []
            if not cs.alive:
                extras.append("已亡")
            if cs.conditions:
                extras.append("/".join(cs.conditions))
            if cs.notes:
                extras.append(cs.notes)
            tag = f"（{'，'.join(extras)}）" if extras else ""
            char_lines.append(f"- {name} Lv{cs.level}{tag}")
        parts.append("角色当前状态：\n" + "\n".join(char_lines))

    if state.world.locations:
        loc_lines = [f"- {k}：{v}" for k, v in state.world.locations.items()]
        parts.append("地点状态：\n" + "\n".join(loc_lines))

    if state.world.factions:
        fac_lines = [f"- {k}：{v}" for k, v in state.world.factions.items()]
        parts.append("势力进展：\n" + "\n".join(fac_lines))

    return "\n\n".join(parts)


def render_worldview_excerpt(wv: Worldview) -> str:
    """从 Worldview 抽出大纲层最有用的信息：禁词 / 风格 / 自定义 lore。"""
    lines: list[str] = [f"系统：{wv.display_name}"]
    if wv.banned_words:
        lines.append("禁词：" + "、".join(wv.banned_words))
    if wv.reference_authors:
        lines.append("参考作家：" + "、".join(wv.reference_authors))
    return "\n".join(lines)


def render_narrative_excerpt(
    scenes: Sequence[Scene],
    events_by_id: dict[str, TaggedEvent],
    *,
    include_roll_outcomes: bool = False,
    max_chars: int | None = None,
) -> str:
    """把 scenes 转成 LLM 可读的叙事流文本。

    - ``include_roll_outcomes=False``：大纲层不需要骰子细节，默认关。
    - ``max_chars``：超过则尾段截断（保留前缀），仅在生成大纲时启用以控上下文。
    """
    feed = build_feed(scenes, events_by_id)
    text = feed_to_text(feed, include_roll_outcomes=include_roll_outcomes)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n…（叙事文本已截断）"
    return text


def render_scene_summary_lines(
    scenes: Sequence[Scene],
    summaries: dict[str, str],
) -> list[str]:
    """把 scene 时间线 + 一句话摘要拼成行列表，供 propose / 卷生成使用。"""
    lines: list[str] = []
    for s in scenes:
        summary = summaries.get(s.id, "").strip() or "（暂无摘要）"
        triggers = "/".join(s.triggers) if s.triggers else "-"
        lines.append(
            f"- [{s.id}] {s.session_id} {s.kind} "
            f"events={s.event_count} triggers={triggers} :: {summary}"
        )
    return lines


def render_rag_excerpt(rag, query_text: str, *, top_k: int = 4) -> str:
    """检索 RAG 知识库的 top-K 片段；rag 缺省/未配置时返回空串。"""
    if rag is None or not query_text.strip():
        return ""
    try:
        chunks = rag.query(query_text, top_k=top_k)
    except Exception:
        return ""
    if not chunks:
        return ""
    parts: list[str] = []
    for ch in chunks:
        parts.append(f"【{ch.source}】{ch.text.strip()}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 高级组合
# ---------------------------------------------------------------------------


def build_outline_context(
    *,
    worldview: Worldview,
    cards: dict[str, CharacterCard],
    state: StoryState,
    scenes: Sequence[Scene] = (),
    events_by_id: dict[str, TaggedEvent] | None = None,
    summaries: dict[str, str] | None = None,
    last_volume_summary: str = "",
    narrative_max_chars: int | None = 60_000,
    rag=None,
    rag_query: str = "",
) -> OutlineContext:
    """一次性组装大纲生成所需的上下文。

    Args:
        scenes: 待入大纲的 scenes（campaign 级可传空，volume 级传该卷 scenes）。
        summaries: ``{scene_id: 一句话摘要}``，用于 scene 时间线行。
        narrative_max_chars: 叙事文本上限；为 None 不截断。
        rag / rag_query: 若提供则注入 top-K 知识库片段。
    """
    summaries = summaries or {}
    events_by_id = events_by_id or {}

    narrative_excerpt = ""
    if scenes and events_by_id:
        narrative_excerpt = render_narrative_excerpt(
            scenes,
            events_by_id,
            include_roll_outcomes=False,
            max_chars=narrative_max_chars,
        )

    return OutlineContext(
        worldview_excerpt=render_worldview_excerpt(worldview),
        custom_lore=worldview.custom_lore,
        pc_facts=render_pc_facts(cards),
        state_summary=render_state_summary(state),
        lore_unlocked=list(state.lore_unlocked),
        narrative_excerpt=narrative_excerpt,
        scene_summary_lines=render_scene_summary_lines(scenes, summaries) if scenes else [],
        last_volume_summary=last_volume_summary,
        rag_excerpt=render_rag_excerpt(rag, rag_query) if rag else "",
    )
