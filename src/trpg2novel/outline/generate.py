"""Outline generate — campaign / volume 大纲生成。

这层负责：
- campaign 级长期大纲的首次生成
- volume 级详细大纲生成
- 将人物卡 / 阵容 / scene 摘要 / narration feed 组装成 prompt
- 通过 LLM 产出可写盘的 dataclass
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from jinja2 import Environment, FileSystemLoader

from trpg2novel.character.card_loader import load_all_cards
from trpg2novel.campaign import Campaign
from trpg2novel.config import StageLLMConfig
from trpg2novel.llm.client import chat_json, make_client
from trpg2novel.outline.context import build_outline_context
from trpg2novel.outline.io import ensure_outline_dirs, load_campaign_outline, save_campaign_outline, save_volume_outline
from trpg2novel.outline.roster import compute_volume_roster
from trpg2novel.outline.schema import CampaignOutline, VolumeOutline
from trpg2novel.outline.scene_summary import load_summary_cache
from trpg2novel.parse.classify import TaggedEvent
from trpg2novel.segment.scene import Scene
from trpg2novel.session_loader import load_players, load_session
from trpg2novel.state.story_state import StoryState
from trpg2novel.worldview import load_worldview_for_campaign

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


def _load_session_configs(camp: Campaign) -> dict[str, object]:
    out: dict[str, object] = {}
    players_cfg = None
    if camp.players_yaml.exists():
        try:
            players_cfg = load_players(camp.players_yaml)
        except Exception:
            players_cfg = None
    for p in sorted(camp.raw_logs_dir.glob("*.yaml")):
        try:
            cfg = load_session(p, players_cfg)
        except Exception:
            continue
        out[cfg.session_id] = cfg
    return out


def _outline_to_prompt(outline: CampaignOutline | VolumeOutline | None) -> str:
    if outline is None:
        return ""
    return json.dumps(asdict(outline), ensure_ascii=False, indent=2)


def generate_campaign_outline(
    camp: Campaign,
    state: StoryState,
    scenes: Sequence[Scene],
    events_by_id: dict[str, TaggedEvent],
    *,
    model_cfg: StageLLMConfig,
    seed_text: str = "",
    force_regenerate: bool = False,
) -> CampaignOutline:
    """生成或重生成 campaign 级长期大纲。"""
    ensure_outline_dirs(camp)
    existing = None if force_regenerate else load_campaign_outline(camp)

    worldview = load_worldview_for_campaign(camp)
    cards = load_all_cards(camp.character_cards_dir)
    summaries = load_summary_cache(camp.parsed_dir)
    ctx = build_outline_context(
        worldview=worldview,
        cards=cards,
        state=state,
        scenes=scenes,
        events_by_id=events_by_id,
        summaries={sid: s.summary for sid, s in summaries.items()},
        rag=None,
        rag_query=seed_text,
    )
    system_prompt = _env.get_template("outline_campaign_system.j2").render()
    user_prompt = _env.get_template("outline_campaign_user.j2").render(
        outline=_outline_to_prompt(existing),
        **ctx.to_prompt_dict(),
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
        max_tokens=int(os.environ.get("LLM_OUTLINE_CAMPAIGN_MAX_TOKENS", "6000")),
    )
    outline = CampaignOutline.from_dict(raw)
    if not outline.based_on_sessions:
        outline.based_on_sessions = sorted({s.session_id for s in scenes})
    save_campaign_outline(camp, outline, snapshot=True)
    return outline


def generate_volume_outline(
    camp: Campaign,
    volume_index: int,
    scenes: Sequence[Scene],
    events_by_id: dict[str, TaggedEvent],
    state: StoryState,
    campaign_outline: CampaignOutline,
    *,
    model_cfg: StageLLMConfig,
    last_volume_summary: str = "",
    force_regenerate: bool = False,
) -> VolumeOutline:
    """生成某卷的详细大纲。"""
    ensure_outline_dirs(camp)
    existing = None
    if not force_regenerate:
        from trpg2novel.outline.io import load_volume_outline
        existing = load_volume_outline(camp, volume_index, prefer_draft=True)

    scene_ids = [s.id for s in scenes]
    session_ids = sorted({s.session_id for s in scenes})
    worldview = load_worldview_for_campaign(camp)
    cards = load_all_cards(camp.character_cards_dir)
    session_cfgs = _load_session_configs(camp)
    roster = compute_volume_roster(
        session_ids,
        cards,
        session_cfgs,
        all_session_ids_ordered=camp.list_sessions(),
        campaign_outline=campaign_outline,
    )
    summaries = load_summary_cache(camp.parsed_dir)
    ctx = build_outline_context(
        worldview=worldview,
        cards=cards,
        state=state,
        scenes=scenes,
        events_by_id=events_by_id,
        summaries={sid: s.summary for sid, s in summaries.items()},
        last_volume_summary=last_volume_summary,
        rag=None,
        rag_query=" ".join(scene_ids),
    )
    system_prompt = _env.get_template("outline_volume_system.j2").render()
    user_prompt = _env.get_template("outline_volume_user.j2").render(
        outline=_outline_to_prompt(existing),
        roster=json.dumps(asdict(roster), ensure_ascii=False, indent=2),
        **ctx.to_prompt_dict(),
    )

    client = make_client(model_cfg.api_key, model_cfg.base_url)
    raw = chat_json(
        client,
        model_cfg.model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.35,
        max_tokens=int(os.environ.get("LLM_OUTLINE_VOLUME_MAX_TOKENS", "6000")),
    )
    outline = VolumeOutline.from_dict(raw)
    outline.volume_index = volume_index
    if not outline.based_on_scenes:
        outline.based_on_scenes = scene_ids
    if not outline.session_ids:
        outline.session_ids = session_ids
    outline.roster = roster
    save_volume_outline(camp, outline, as_draft=True, snapshot=True)
    return outline
