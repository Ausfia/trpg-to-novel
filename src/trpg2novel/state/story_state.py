"""[4] Story State 最小版 — 跨场次滚动状态 (characters.status / lore_unlocked)。

设计原则：
- `story_state.yaml` 是单一事实源。工具只追加 diff，不自动覆写。
- 每场跑完后，脚本/用户把"本场发生的状态变更"追加到 state，git 可审计。
- MVP 只跟踪：
    characters.<name>.alive (bool)
    characters.<name>.level (int)
    characters.<name>.conditions: list[str]  # 力竭-2、伤者等
    characters.<name>.notes: str  # 自由文本
    lore_unlocked: list[str]  # 玩家角色已知的设定事实
    world.locations: dict[str, str]  # 地点当前状态
    world.factions: dict[str, str]   # 势力事件进展
    session_log: list  # 已处理的 session_id 列表
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CharacterStatus:
    alive: bool = True
    level: int = 1
    conditions: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class WorldState:
    locations: dict[str, str] = field(default_factory=dict)
    factions: dict[str, str] = field(default_factory=dict)


@dataclass
class VolumeRecord:
    """单卷的运行时元信息（与 outline/volumes/vol{NN}.yaml 互补）。

    status 推进路径：proposed → draft → confirmed → drafting → closed
    """

    volume_index: int
    status: str = "proposed"          # proposed / draft / confirmed / drafting / closed
    outline_path: str = ""
    skeleton_path: str | None = None
    scene_ids: list[str] = field(default_factory=list)
    chapter_indices: list[int] = field(default_factory=list)
    word_count: int | None = None
    confirmed_at: str | None = None
    closed_at: str | None = None
    proposal_reasoning: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "VolumeRecord":
        return cls(
            volume_index=int(data.get("volume_index", 0)),
            status=data.get("status", "proposed"),
            outline_path=data.get("outline_path", ""),
            skeleton_path=data.get("skeleton_path"),
            scene_ids=list(data.get("scene_ids") or []),
            chapter_indices=list(data.get("chapter_indices") or []),
            word_count=data.get("word_count"),
            confirmed_at=data.get("confirmed_at"),
            closed_at=data.get("closed_at"),
            proposal_reasoning=data.get("proposal_reasoning", ""),
        )

    def to_dict(self) -> dict:
        return {
            "volume_index": self.volume_index,
            "status": self.status,
            "outline_path": self.outline_path,
            "skeleton_path": self.skeleton_path,
            "scene_ids": list(self.scene_ids),
            "chapter_indices": list(self.chapter_indices),
            "word_count": self.word_count,
            "confirmed_at": self.confirmed_at,
            "closed_at": self.closed_at,
            "proposal_reasoning": self.proposal_reasoning,
        }


@dataclass
class PendingScenePool:
    """LLM 在 propose 阶段判定"不足成卷"的 scene 池。"""

    scene_ids: list[str] = field(default_factory=list)
    reason: str = ""
    last_proposed_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "PendingScenePool":
        return cls(
            scene_ids=list(data.get("scene_ids") or []),
            reason=data.get("reason", ""),
            last_proposed_at=data.get("last_proposed_at", ""),
        )

    def to_dict(self) -> dict:
        return {
            "scene_ids": list(self.scene_ids),
            "reason": self.reason,
            "last_proposed_at": self.last_proposed_at,
        }


@dataclass
class StoryState:
    characters: dict[str, CharacterStatus] = field(default_factory=dict)
    world: WorldState = field(default_factory=WorldState)
    lore_unlocked: list[str] = field(default_factory=list)
    session_log: list[str] = field(default_factory=list)
    # v3.1: 已入章的场景 id 与章节台账（用于自动续章 / UI 渲染）
    processed_scene_ids: list[str] = field(default_factory=list)
    chapter_index: list[dict] = field(default_factory=list)
    # v3.2 (PR1b): 卷生命周期、pending 池、campaign 大纲版本追踪
    current_volume_index: int = 0
    volumes: list[VolumeRecord] = field(default_factory=list)
    pending_pool: PendingScenePool | None = None
    last_campaign_outline_update_sessions: list[str] = field(default_factory=list)


def load_state(path: Path) -> StoryState:
    """从 YAML 文件加载 StoryState，文件不存在时返回空初始状态。"""
    if not path.exists():
        return StoryState()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    chars: dict[str, CharacterStatus] = {}
    for name, data in raw.get("characters", {}).items():
        chars[name] = CharacterStatus(
            alive=data.get("alive", True),
            level=data.get("level", 1),
            conditions=list(data.get("conditions", [])),
            notes=data.get("notes", ""),
        )
    world_raw = raw.get("world", {})
    world = WorldState(
        locations=dict(world_raw.get("locations", {})),
        factions=dict(world_raw.get("factions", {})),
    )
    pending_raw = raw.get("pending_pool")
    pending = PendingScenePool.from_dict(pending_raw) if pending_raw else None
    return StoryState(
        characters=chars,
        world=world,
        lore_unlocked=list(raw.get("lore_unlocked", [])),
        session_log=list(raw.get("session_log", [])),
        processed_scene_ids=list(raw.get("processed_scene_ids", [])),
        chapter_index=list(raw.get("chapter_index", [])),
        current_volume_index=int(raw.get("current_volume_index", 0)),
        volumes=[VolumeRecord.from_dict(v) for v in (raw.get("volumes") or [])],
        pending_pool=pending,
        last_campaign_outline_update_sessions=list(
            raw.get("last_campaign_outline_update_sessions", [])
        ),
    )


def save_state(state: StoryState, path: Path) -> None:
    """保存 StoryState 到 YAML。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "characters": {
            name: asdict(cs)
            for name, cs in state.characters.items()
        },
        "world": asdict(state.world),
        "lore_unlocked": state.lore_unlocked,
        "session_log": state.session_log,
        "processed_scene_ids": state.processed_scene_ids,
        "chapter_index": state.chapter_index,
        "current_volume_index": state.current_volume_index,
        "volumes": [v.to_dict() for v in state.volumes],
        "pending_pool": state.pending_pool.to_dict() if state.pending_pool else None,
        "last_campaign_outline_update_sessions": list(
            state.last_campaign_outline_update_sessions
        ),
    }
    path.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def apply_patch(state: StoryState, patch: dict[str, Any]) -> StoryState:
    """把一个 diff patch 合并到 state（shallow），返回新 state 对象。

    patch 格式（所有字段可选）：
    {
        "characters": {
            "雷恩": {"alive": true, "level": 2, "conditions": ["力竭-2"]},
            ...
        },
        "lore_unlocked": ["凡人会死后起死回生"],
        "world": {
            "locations": {"至绿镇": "被袭击后基本完整"},
            "factions": {"龙巫教": "已撤退"}
        },
        "session_log": ["s01"]
    }
    """
    import copy
    state = copy.deepcopy(state)

    for name, diff in patch.get("characters", {}).items():
        if name not in state.characters:
            state.characters[name] = CharacterStatus()
        cs = state.characters[name]
        if "alive" in diff:
            cs.alive = bool(diff["alive"])
        if "level" in diff:
            cs.level = int(diff["level"])
        if "conditions" in diff:
            cs.conditions = list(diff["conditions"])
        if "notes" in diff:
            cs.notes = str(diff["notes"])

    for fact in patch.get("lore_unlocked", []):
        if fact not in state.lore_unlocked:
            state.lore_unlocked.append(fact)

    world_patch = patch.get("world", {})
    state.world.locations.update(world_patch.get("locations", {}))
    state.world.factions.update(world_patch.get("factions", {}))

    for sid in patch.get("session_log", []):
        if sid not in state.session_log:
            state.session_log.append(sid)

    return state
