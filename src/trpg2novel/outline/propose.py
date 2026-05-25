"""Outline propose — scene summaries → 多卷边界提议。"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from trpg2novel.config import StageLLMConfig
from trpg2novel.llm.client import chat_json, make_client
from trpg2novel.outline.schema import CampaignOutline, VolumeOutline
from trpg2novel.outline.scene_summary import SceneSummary, load_summary_cache
from trpg2novel.segment.scene import Scene
from trpg2novel.state.story_state import StoryState

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


@dataclass
class VolumeProposal:
    scene_id_range: tuple[str, str]
    scene_ids: list[str]
    working_title: str
    theme_summary: str
    emotion_arc_outline: list[str] = field(default_factory=list)
    target_word_count_estimate: int = 0
    reasoning: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "VolumeProposal":
        scene_id_range = data.get("scene_id_range") or []
        if isinstance(scene_id_range, list):
            scene_id_range = tuple(scene_id_range[:2]) if len(scene_id_range) >= 2 else tuple(scene_id_range + [""])
        if len(scene_id_range) != 2:
            scene_id_range = ("", "")
        return cls(
            scene_id_range=(str(scene_id_range[0]), str(scene_id_range[1])),
            scene_ids=list(data.get("scene_ids") or []),
            working_title=data.get("working_title", ""),
            theme_summary=data.get("theme_summary", ""),
            emotion_arc_outline=list(data.get("emotion_arc_outline") or []),
            target_word_count_estimate=int(data.get("target_word_count_estimate", 0)),
            reasoning=data.get("reasoning", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VolumeProposalResult:
    proposed_volumes: list[VolumeProposal] = field(default_factory=list)
    pending_scenes: list[str] = field(default_factory=list)
    pending_reason: str = ""
    based_on_scene_count: int = 0
    batch_count: int = 1

    @classmethod
    def from_dict(cls, data: dict) -> "VolumeProposalResult":
        return cls(
            proposed_volumes=[VolumeProposal.from_dict(v) for v in (data.get("proposed_volumes") or [])],
            pending_scenes=list(data.get("pending_scenes") or []),
            pending_reason=data.get("pending_reason", ""),
            based_on_scene_count=int(data.get("based_on_scene_count", 0)),
            batch_count=int(data.get("batch_count", 1)),
        )

    def to_dict(self) -> dict:
        return {
            "proposed_volumes": [v.to_dict() for v in self.proposed_volumes],
            "pending_scenes": list(self.pending_scenes),
            "pending_reason": self.pending_reason,
            "based_on_scene_count": self.based_on_scene_count,
            "batch_count": self.batch_count,
        }


def _scene_summary_text(scene: Scene, summaries: dict[str, str | SceneSummary]) -> str:
    existing = summaries.get(scene.id)
    if isinstance(existing, SceneSummary):
        text = existing.summary.strip()
    elif isinstance(existing, str):
        text = existing.strip()
    else:
        text = ""
    if text:
        return text
    trigger_text = "、".join(scene.triggers[:3]) if getattr(scene, "triggers", None) else ""
    if trigger_text:
        return f"{scene.kind} 场景，{scene.event_count} 条事件，触发点：{trigger_text}"
    return f"{scene.kind} 场景，{scene.event_count} 条事件"


def _scene_payload(scene: Scene, summaries: dict[str, str | SceneSummary]) -> dict:
    return {
        "scene_id": scene.id,
        "session_id": scene.session_id,
        "kind": scene.kind,
        "event_count": scene.event_count,
        "triggers": list(getattr(scene, "triggers", []) or []),
        "summary": _scene_summary_text(scene, summaries),
    }


def _heuristic_proposals(
    scenes: list[Scene],
    summaries: dict[str, str | SceneSummary],
    *,
    target_scenes_per_volume: tuple[int, int] = (5, 12),
    hint_scene_id: str | None = None,
) -> VolumeProposalResult:
    min_scenes, max_scenes = target_scenes_per_volume
    ordered = list(scenes)
    total = len(ordered)
    if total < min_scenes:
        return VolumeProposalResult(
            proposed_volumes=[],
            pending_scenes=[s.id for s in ordered],
            pending_reason="场景数不足成卷，等待后续场景补足。",
            based_on_scene_count=total,
            batch_count=1,
        )

    hint_idx = None
    if hint_scene_id:
        for idx, scene in enumerate(ordered):
            if scene.id == hint_scene_id:
                hint_idx = idx
                break

    chunks: list[list[Scene]] = []
    start = 0
    if hint_idx is not None and hint_idx + 1 < total:
        chunks.append(ordered[: hint_idx + 1])
        start = hint_idx + 1

    group_size = min(6, max_scenes)
    while start < total:
        end = min(start + group_size, total)
        chunks.append(ordered[start:end])
        start = end

    if chunks and len(chunks[-1]) < min_scenes and len(chunks) > 1:
        chunks[-2].extend(chunks[-1])
        chunks.pop()

    proposals: list[VolumeProposal] = []
    for idx, chunk in enumerate(chunks, start=1):
        if not chunk:
            continue
        first_sid = chunk[0].id
        last_sid = chunk[-1].id
        first_summary = _scene_summary_text(chunk[0], summaries)
        last_summary = _scene_summary_text(chunk[-1], summaries)
        theme_summary = first_summary if first_sid == last_sid else f"{first_summary} → {last_summary}"
        proposals.append(
            VolumeProposal(
                scene_id_range=(first_sid, last_sid),
                scene_ids=[s.id for s in chunk],
                working_title=f"卷{idx:02d}：{chunk[0].session_id}~{chunk[-1].session_id}",
                theme_summary=theme_summary,
                emotion_arc_outline=["开端", "推进", "转折", "收束"] if len(chunk) >= 4 else ["开端", "推进", "收束"],
                target_word_count_estimate=len(chunk) * 3000,
                reasoning=f"按约 {group_size} 个 scene 递进切分，保持卷内弧线完整。",
            )
        )

    pending_scenes: list[str] = []
    pending_reason = ""
    if proposals and len(proposals[-1].scene_ids) < min_scenes:
        pending_scenes = proposals.pop().scene_ids
        pending_reason = "尾段 scene 数不足成卷，留待后续日志补足。"

    return VolumeProposalResult(
        proposed_volumes=proposals,
        pending_scenes=pending_scenes,
        pending_reason=pending_reason,
        based_on_scene_count=total,
        batch_count=max(1, math.ceil(total / 30)),
    )


def _prompt_payload(
    scenes: list[Scene],
    summaries: dict[str, str | SceneSummary],
    campaign_outline: CampaignOutline,
    state: StoryState,
    hint_scene_id: str | None,
) -> dict[str, str]:
    scene_payloads = [_scene_payload(scene, summaries) for scene in scenes]
    state_payload = {
        "current_volume_index": state.current_volume_index,
        "processed_scene_ids": list(state.processed_scene_ids),
        "volumes": [v.to_dict() for v in state.volumes],
        "pending_pool": state.pending_pool.to_dict() if state.pending_pool else None,
        "last_campaign_outline_update_sessions": list(state.last_campaign_outline_update_sessions),
    }
    payload = {
        "campaign_outline_json": json.dumps(campaign_outline.to_dict(), ensure_ascii=False, indent=2),
        "state_json": json.dumps(state_payload, ensure_ascii=False, indent=2),
        "scene_timeline_json": json.dumps(scene_payloads, ensure_ascii=False, indent=2),
        "hint_scene_id": hint_scene_id or "",
    }
    return payload


def _call_llm(
    scenes: list[Scene],
    summaries: dict[str, str | SceneSummary],
    campaign_outline: CampaignOutline,
    state: StoryState,
    *,
    model_cfg: StageLLMConfig,
    hint_scene_id: str | None,
) -> VolumeProposalResult | None:
    if not model_cfg.api_key.strip():
        return None
    system_prompt = _env.get_template("outline_volumes_propose_system.j2").render()
    user_prompt = _env.get_template("outline_volumes_propose_user.j2").render(
        **_prompt_payload(scenes, summaries, campaign_outline, state, hint_scene_id)
    )
    client = make_client(model_cfg.api_key, model_cfg.base_url)
    raw = chat_json(
        client,
        model_cfg.model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.25,
        max_tokens=6000,
    )
    try:
        result = VolumeProposalResult.from_dict(raw)
    except Exception:
        return None
    if not result.proposed_volumes and not result.pending_scenes:
        return None
    return result


def propose_volumes(
    unprocessed_scenes: list[Scene],
    summaries: dict[str, str | SceneSummary],
    campaign_outline: CampaignOutline,
    state: StoryState,
    *,
    max_scenes_per_batch: int = 30,
    target_scenes_per_volume: tuple[int, int] = (5, 12),
    model_cfg: StageLLMConfig | None = None,
    hint_scene_id: str | None = None,
) -> VolumeProposalResult:
    """把待处理 scene 划成若干卷提议。"""
    ordered = list(unprocessed_scenes)
    if not ordered:
        return VolumeProposalResult(based_on_scene_count=0, batch_count=0)

    if model_cfg is not None and model_cfg.api_key.strip():
        result = _call_llm(
            ordered,
            summaries,
            campaign_outline,
            state,
            model_cfg=model_cfg,
            hint_scene_id=hint_scene_id,
        )
        if result is not None:
            if not result.based_on_scene_count:
                result.based_on_scene_count = len(ordered)
            if not result.batch_count:
                result.batch_count = max(1, math.ceil(len(ordered) / max_scenes_per_batch))
            return result

    return _heuristic_proposals(
        ordered,
        summaries,
        target_scenes_per_volume=target_scenes_per_volume,
        hint_scene_id=hint_scene_id,
    )


def proposal_to_outline(
    proposal: VolumeProposal,
    *,
    volume_index: int,
    scenes: list[Scene],
) -> VolumeOutline:
    session_ids = sorted({scene.session_id for scene in scenes if scene.id in set(proposal.scene_ids)})
    return VolumeOutline(
        volume_index=volume_index,
        based_on_scenes=list(proposal.scene_ids),
        scene_range=list(proposal.scene_id_range),
        session_ids=session_ids,
        target_word_count_estimate=proposal.target_word_count_estimate,
        target_chapter_count_estimate=max(1, round(proposal.target_word_count_estimate / 5000)) if proposal.target_word_count_estimate else max(1, len(proposal.scene_ids) // 2 or 1),
        working_title=proposal.working_title,
        theme_summary=proposal.theme_summary,
        emotion_arc=[],
        key_beats=[],
        subplots=[],
        pacing_notes=[proposal.reasoning] if proposal.reasoning else [],
        ending_strategy="closed",
        proposal_reasoning=proposal.reasoning,
        status="proposed",
    )
