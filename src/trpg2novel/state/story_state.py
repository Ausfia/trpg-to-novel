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
class StoryState:
    characters: dict[str, CharacterStatus] = field(default_factory=dict)
    world: WorldState = field(default_factory=WorldState)
    lore_unlocked: list[str] = field(default_factory=list)
    session_log: list[str] = field(default_factory=list)
    # v3.1: 已入章的场景 id 与章节台账（用于自动续章 / UI 渲染）
    processed_scene_ids: list[str] = field(default_factory=list)
    chapter_index: list[dict] = field(default_factory=list)


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
    return StoryState(
        characters=chars,
        world=world,
        lore_unlocked=list(raw.get("lore_unlocked", [])),
        session_log=list(raw.get("session_log", [])),
        processed_scene_ids=list(raw.get("processed_scene_ids", [])),
        chapter_index=list(raw.get("chapter_index", [])),
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
