"""Outline revise — 增量修订 campaign 大纲。

流程：
1. ``sync_key_characters_from_cards`` 产出 RosterChange 列表（已有）。
2. ``propose_campaign_revision`` 把 roster changes + 新 session 叙事文本送 LLM，
   产出 arc/narrative 修订提议，连同 roster changes 打包为 CampaignRevisionProposal。
3. 用户在 UI 勾选接受项后，调用 ``apply_revision`` 落盘。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from jinja2 import Environment, FileSystemLoader

from trpg2novel.campaign import Campaign
from trpg2novel.character.card_loader import CharacterCard, load_all_cards
from trpg2novel.config import StageLLMConfig
from trpg2novel.llm.client import chat_json, make_client
from trpg2novel.outline.io import load_campaign_outline, save_campaign_outline
from trpg2novel.outline.roster import (
    RosterChange,
    apply_key_character_changes,
    sync_key_characters_from_cards,
)
from trpg2novel.outline.schema import CampaignOutline, EvolutionNote, MajorArc
from trpg2novel.outline.context import (
    build_outline_context,
    render_narrative_excerpt,
    render_pc_facts,
    render_state_summary,
)
from trpg2novel.parse.classify import TaggedEvent
from trpg2novel.segment.scene import Scene
from trpg2novel.state.story_state import StoryState
from trpg2novel.worldview import load_worldview_for_campaign

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class CampaignRevisionProposal:
    """LLM 产出的修订提议，含 roster 同步结果。"""

    arc_updates: list[dict] = field(default_factory=list)
    narrative_notes: list[dict] = field(default_factory=list)
    new_sessions_summary: str = ""
    roster_impact_narrative: str = ""
    roster_changes: list[dict] = field(default_factory=list)
    new_sessions: list[str] = field(default_factory=list)

    @classmethod
    def from_llm_response(cls, raw: dict, roster_changes: list[RosterChange], new_sessions: list[str]) -> "CampaignRevisionProposal":
        return cls(
            arc_updates=list(raw.get("arc_updates") or []),
            narrative_notes=list(raw.get("narrative_notes") or []),
            new_sessions_summary=raw.get("new_sessions_summary", ""),
            roster_impact_narrative=raw.get("roster_impact_narrative", ""),
            roster_changes=[ch.to_dict() for ch in roster_changes],
            new_sessions=list(new_sessions),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def has_any(self) -> bool:
        return bool(
            self.arc_updates
            or self.narrative_notes
            or self.roster_changes
            or self.new_sessions_summary
        )


# ---------------------------------------------------------------------------
# propose_campaign_revision
# ---------------------------------------------------------------------------


def propose_campaign_revision(
    camp: Campaign,
    state: StoryState,
    scenes: Sequence[Scene],
    events_by_id: dict[str, TaggedEvent],
    *,
    model_cfg: StageLLMConfig,
) -> CampaignRevisionProposal:
    """对比人物卡与大纲，产出修订提议（含 LLM 叙事修订）。"""
    outline = load_campaign_outline(camp)
    if outline is None:
        raise FileNotFoundError("先运行 outline campaign，再运行 revise。")

    cards = load_all_cards(camp.character_cards_dir)
    all_session_ids = camp.list_sessions()

    # 1) 阵容同步
    roster_changes = sync_key_characters_from_cards(outline, cards, all_session_ids)

    # 2) 找出新 sessions（自上次大纲更新以来）
    known = set(outline.based_on_sessions or [])
    new_sessions = [sid for sid in all_session_ids if sid not in known]
    if not new_sessions and not roster_changes:
        # 无变化，返回空提议
        return CampaignRevisionProposal(
            new_sessions=[],
            roster_changes=[ch.to_dict() for ch in roster_changes],
        )

    # 3) 组装 LLM 上下文
    worldview = load_worldview_for_campaign(camp)
    new_scenes = [s for s in scenes if s.session_id in set(new_sessions)]

    narrative_excerpt = ""
    if new_scenes and events_by_id:
        narrative_excerpt = render_narrative_excerpt(
            new_scenes, events_by_id, include_roll_outcomes=False, max_chars=40_000,
        )

    system_prompt = _env.get_template("outline_campaign_revise_system.j2").render()
    user_prompt = _env.get_template("outline_campaign_revise_user.j2").render(
        campaign_outline_json=json.dumps(outline.to_dict(), ensure_ascii=False, indent=2),
        roster_changes_json=json.dumps(
            [ch.to_dict() for ch in roster_changes],
            ensure_ascii=False,
            indent=2,
        ),
        new_sessions=", ".join(new_sessions) if new_sessions else "（无新增 session）",
        narrative_excerpt=narrative_excerpt,
        state_summary=render_state_summary(state),
    )

    client = make_client(model_cfg.api_key, model_cfg.base_url)
    raw = chat_json(
        client,
        model_cfg.model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=4000,
    )

    return CampaignRevisionProposal.from_llm_response(raw, roster_changes, new_sessions)


# ---------------------------------------------------------------------------
# apply_revision
# ---------------------------------------------------------------------------


def apply_revision(
    camp: Campaign,
    proposal: CampaignRevisionProposal,
    *,
    accept_arc_ids: set[str] | None = None,
    accept_narrative_keys: set[str] | None = None,
    accept_roster_names: set[str] | None = None,
) -> CampaignOutline:
    """把用户接受的修订项合并进 campaign 大纲并写盘。

    Args:
        accept_arc_ids: 接受哪些 arc_update（按 arc_id 匹配）。
        accept_narrative_keys: 接受哪些 narrative_note（按 key 匹配）。
        accept_roster_names: 接受哪些 roster_change（按 name 匹配；None 或空集 = 都不接受）。
    """
    outline = load_campaign_outline(camp)
    if outline is None:
        raise FileNotFoundError("找不到 campaign 大纲。")

    accept_arc_ids = accept_arc_ids or set()
    accept_narrative_keys = accept_narrative_keys or set()
    accept_roster_names = accept_roster_names or set()

    changes_desc: list[str] = []

    # —— arc 更新 ——
    arc_by_id = {a.id: a for a in outline.major_arcs}
    for update in proposal.arc_updates:
        arc_id = update.get("arc_id", "")
        if arc_id not in accept_arc_ids:
            continue
        arc = arc_by_id.get(arc_id)
        if arc is None:
            # 新 arc
            arc = MajorArc(id=arc_id, name=update.get("name", arc_id))
            outline.major_arcs.append(arc)
            arc_by_id[arc_id] = arc
        new_status = update.get("new_status", "")
        new_summary = update.get("new_summary", "")
        if new_status and arc.status != new_status:
            changes_desc.append(f"arc {arc_id}: {arc.status} → {new_status}")
            arc.status = new_status
        if new_summary and arc.summary != new_summary:
            arc.summary = new_summary
            if arc_id not in {d.split(":")[0].split()[1] for d in changes_desc}:
                changes_desc.append(f"arc {arc_id} summary updated")

    # —— narrative notes ——
    for note in proposal.narrative_notes:
        key = note.get("key", "")
        if key in accept_narrative_keys:
            changes_desc.append(f"narrative: {note.get('summary', key)}")

    # —— roster 变更 ——
    if accept_roster_names:
        roster_changes = [
            RosterChange(
                name=ch["name"],
                change_type=ch.get("change_type", ""),
                before=ch.get("before"),
                after=ch.get("after"),
                description=ch.get("description", ""),
            )
            for ch in proposal.roster_changes
            if ch.get("name") in accept_roster_names
        ]
        apply_key_character_changes(outline, roster_changes)
        for ch in roster_changes:
            changes_desc.append(f"roster: {ch.description}")

    # —— 记录 evolution_note ——
    if changes_desc:
        outline.evolution_notes.append(
            EvolutionNote(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                triggered_by_sessions=list(proposal.new_sessions),
                summary="; ".join(changes_desc),
                roster_changes=[ch.get("description", "") for ch in proposal.roster_changes],
            )
        )

    # —— 更新 based_on_sessions ——
    for sid in proposal.new_sessions:
        if sid not in outline.based_on_sessions:
            outline.based_on_sessions.append(sid)

    # —— 清除 pending — 若没有剩余未接受的变更则清掉 ——
    outline.pending_revision = None

    save_campaign_outline(camp, outline, snapshot=True)
    return outline
