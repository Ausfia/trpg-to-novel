"""[3] Scene Segment — 把 TaggedEvent 序列切成场景（Scene）列表。

切分规则（纯规则，无 LLM）：
1. 战斗开始：遇到 initiative_list segment → 开新 battle scene
2. 战斗结束：遇到 initiative_clear segment → 结束 battle，开新 narration scene
3. DM 时间/地点转折词：DM dm_narration 含预设关键词 → 开新 narration scene
4. 时间间隙：相邻两条事件时间差 > gap_threshold_seconds → 开新 narration scene
5. 文件首条事件 → 起始 narration scene
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from trpg2novel.parse.classify import TaggedEvent

# DM 叙述中触发场景转折的关键词
DM_TRANSITION_RE = re.compile(
    r"次日|翌日|第二天|天刚蒙|清晨|黄昏|深夜|凌晨|夜晚|夜深|"
    r"一天后|数日后|几日后|片刻后|不久之后|一段时间后|"
    r"地点.转[移换]|时间流逝|话题一转|镜头切"
)

BATTLE_KIND = "battle"
NARRATION_KIND = "narration"


@dataclass
class Scene:
    id: str          # e.g., "s01-scene-001"
    session_id: str
    kind: str        # narration / battle
    start_ts: str
    end_ts: str
    event_ids: list[str]
    triggers: list[str] = field(default_factory=list)  # 产生本边界的原因

    @property
    def event_count(self) -> int:
        return len(self.event_ids)


def segment_scenes(
    events: Sequence[TaggedEvent],
    session_id: str,
    gap_threshold_seconds: int = 300,
) -> list[Scene]:
    """主入口：返回 Scene 列表，保证覆盖所有 event。"""
    if not events:
        return []

    scenes: list[Scene] = []
    current_kind = NARRATION_KIND
    current_events: list[str] = []
    current_triggers: list[str] = ["session_start"]
    in_battle = False
    prev_dt: datetime | None = None
    prev_ev: TaggedEvent = events[0]

    def parse_ts(ts: str) -> datetime:
        return datetime.strptime(ts, "%H:%M:%S")

    def flush(new_kind: str, new_triggers: list[str], last_event: TaggedEvent) -> None:
        """结束当前场景（若有内容）并切换到 new_kind。"""
        nonlocal current_events, current_kind, current_triggers
        if current_events:
            start_ev_obj = next(e for e in events if e.id == current_events[0])
            idx = len(scenes) + 1
            scenes.append(
                Scene(
                    id=f"{session_id}-scene-{idx:03d}",
                    session_id=session_id,
                    kind=current_kind,
                    start_ts=start_ev_obj.timestamp,
                    end_ts=last_event.timestamp,
                    event_ids=list(current_events),
                    triggers=list(current_triggers),
                )
            )
            current_events = []
        current_kind = new_kind
        current_triggers = new_triggers

    def seg_kinds_of(ev: TaggedEvent) -> set[str]:
        s0 = ev.segments[0]
        if isinstance(s0, dict):
            return {s["kind"] for s in ev.segments}
        return {s.kind for s in ev.segments}

    for ev in events:
        ts = ev.timestamp
        dt = parse_ts(ts)

        # 1. 时间间隙检测
        if prev_dt is not None:
            gap = (dt - prev_dt).total_seconds()
            if gap < 0:
                gap += 86400  # 跨夜
            if gap > gap_threshold_seconds and not in_battle:
                flush(NARRATION_KIND, [f"time_gap:{gap:.0f}s"], prev_ev)

        kinds = seg_kinds_of(ev)

        if "initiative_list" in kinds and not in_battle:
            # 战斗开始：flush pending narration，切换到 battle
            flush(BATTLE_KIND, ["battle_start:initiative_list"], prev_ev)
            in_battle = True
        elif "initiative_clear" in kinds and in_battle:
            # 战斗结束：initiative_clear 归入战斗场景，然后切换到 narration
            current_events.append(ev.id)
            flush(NARRATION_KIND, ["battle_end:initiative_clear"], ev)
            in_battle = False
            prev_dt = dt
            prev_ev = ev
            continue
        elif not in_battle and ev.source == "dm":
            # DM 时间/地点转折词
            dm_text = _dm_text(ev)
            m = DM_TRANSITION_RE.search(dm_text)
            if m and current_events:
                flush(NARRATION_KIND, [f"dm_transition:{m.group()}"], prev_ev)

        current_events.append(ev.id)
        prev_dt = dt
        prev_ev = ev

    # 结尾：提交最后一个场景
    if current_events:
        start_ev_obj = next(e for e in events if e.id == current_events[0])
        idx = len(scenes) + 1
        scenes.append(
            Scene(
                id=f"{session_id}-scene-{idx:03d}",
                session_id=session_id,
                kind=current_kind,
                start_ts=start_ev_obj.timestamp,
                end_ts=events[-1].timestamp,
                event_ids=list(current_events),
                triggers=current_triggers,
            )
        )

    return scenes


def _dm_text(ev: TaggedEvent) -> str:
    s0 = ev.segments[0]
    if isinstance(s0, dict):
        return " ".join(s["text"] for s in ev.segments if s["kind"] == "dm_narration")
    return " ".join(s.text for s in ev.segments if s.kind == "dm_narration")


def scenes_to_json(scenes: list[Scene]) -> str:
    return json.dumps([asdict(s) for s in scenes], ensure_ascii=False, indent=2)


def save_scenes(scenes: list[Scene], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(scenes_to_json(scenes), encoding="utf-8")
