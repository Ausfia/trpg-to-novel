"""从 TaggedEvent 提取适合送给 LLM 的叙事素材（narrative feed）。

过滤掉纯机制内容（骰子数字、先攻列表、轮转标记、OOC 吐槽、图片占位），
保留叙事骨架：DM 叙述 / PC 行动+对话 / 战斗结果（文字提示）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from trpg2novel.parse.classify import TaggedEvent
from trpg2novel.segment.scene import Scene

# 机制词过滤（用于 roll_result 文本降噪）
MECHANIC_WORD_RE = re.compile(
    r"\b(?:HP|AC|DC|D\d+|d\d+|先攻|检定|法术位|回合|命中|豁免|ATK)\b",
    re.IGNORECASE,
)


@dataclass
class NarrativeEntry:
    source: str    # dm / pc / combat_result
    speaker: str
    kind: str      # narration / dialogue / action / combat_onset / combat_end / roll_outcome
    text: str
    event_id: str = ""   # 原始事件 ID（如 "s01-42"），留空表示未关联


def build_feed(
    scenes: Sequence[Scene],
    events_by_id: dict[str, TaggedEvent],
) -> list[NarrativeEntry]:
    """把若干场景的 TaggedEvent 转成有序 NarrativeEntry 列表，每条绑定 event_id。"""
    entries: list[NarrativeEntry] = []
    for scene in scenes:
        for eid in scene.event_ids:
            ev = events_by_id.get(eid)
            if ev is None:
                continue
            for entry in _ev_to_entries(ev):
                entry.event_id = eid
                entries.append(entry)
    return entries


def _ev_to_entries(ev: TaggedEvent) -> list[NarrativeEntry]:
    entries: list[NarrativeEntry] = []
    for seg in ev.segments:
        kind = seg.kind if isinstance(seg, object) and hasattr(seg, "kind") else seg["kind"]
        text = seg.text if hasattr(seg, "text") else seg["text"]

        if kind == "dm_narration":
            if text.strip():
                entries.append(NarrativeEntry("dm", ev.speaker, "narration", text.strip()))
        elif kind == "pc_dialogue":
            entries.append(NarrativeEntry("pc", ev.speaker, "dialogue", text.strip()))
        elif kind == "pc_action":
            entries.append(NarrativeEntry("pc", ev.speaker, "action", text.strip()))
        elif kind == "roll_result":
            # 提取有意义的结果文本，去掉纯数字行
            clean = _clean_roll_result(text, ev.speaker)
            if clean:
                entries.append(NarrativeEntry("pc", ev.speaker, "roll_outcome", clean))
        elif kind == "battle_marker":
            phase = seg.extra.get("phase") if hasattr(seg, "extra") else seg.get("extra", {}).get("phase")
            if phase == "start":
                entries.append(NarrativeEntry("dm", ev.speaker, "combat_onset", text.strip()))
            elif phase == "end":
                entries.append(NarrativeEntry("dm", ev.speaker, "combat_end", text.strip()))
        elif kind == "initiative_list":
            # 提取参战角色名单（去掉技术数字）
            participants = _extract_initiative_participants(text)
            if participants:
                entries.append(
                    NarrativeEntry("dm", ev.speaker, "combat_onset", f"战斗开始。参战方：{participants}")
                )
        # 跳过 pc_ooc / roll_cmd / turn_marker / image / image_meta /
        # initiative_clear / record_meta / control_cmd / unknown / bot_state
    return entries


def _clean_roll_result(text: str, speaker: str) -> str:
    """从骰娘骰子结果文本中提取叙事提示（去掉冗余机制数字）。"""
    # 只保留类似 "<X>掷出了 ...=Y" 中的最终结果部分、以及"进行了攻击"短语
    lines = text.splitlines()
    meaningful: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 过滤纯数字行和先攻数字行
        if re.match(r"^\d+[\\.．]\s*\w+:", line):
            continue
        # 保留类似 "<X>掷出了 ...=N" 的结果行
        m = re.search(r"掷出了.*?=(\d+)", line)
        if m:
            # 只取最后结果数字
            total = m.group(1)
            # 提取动作词
            action_m = re.search(r">([^<>]+)掷出了", line)
            action = action_m.group(1).strip() if action_m else ""
            meaningful.append(f"{ev_name_from_text(line)}掷骰{('·' + action) if action else ''}→{total}")
            continue
        # 骰娘对话文本（"我明白了。"希罗...）— 跳过
        if any(kw in line for kw in ("我明白了", "骰塔", "希罗", "摩卡")):
            continue
        if re.search(r"对.*进行了", line):
            meaningful.append(line.replace("\n", " ").strip())
    return " | ".join(meaningful) if meaningful else ""


def ev_name_from_text(text: str) -> str:
    m = re.search(r"<([^>]+)>", text)
    return m.group(1) if m else "?"


def _extract_initiative_participants(text: str) -> str:
    names: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\d+[\\.．]\s*(\S+):", line.strip())
        if m:
            names.append(m.group(1))
    return "、".join(names)


def feed_to_text(
    entries: list[NarrativeEntry],
    include_roll_outcomes: bool = True,
    include_event_ids: bool = False,
) -> str:
    """把 NarrativeEntry 列表序列化成纯文本供 LLM 消化。

    Args:
        include_event_ids: 若为 True，每行前缀 ``[e:<event_id>]``，供 align 阶段引用。
    """
    lines: list[str] = []
    for e in entries:
        if e.kind == "narration":
            line = f"[DM] {e.text}"
        elif e.kind == "dialogue":
            line = f'[{e.speaker}] "{e.text}"'
        elif e.kind == "action":
            line = f"[{e.speaker}·行动] {e.text}"
        elif e.kind == "roll_outcome" and include_roll_outcomes:
            line = f"[掷骰] {e.text}"
        elif e.kind == "combat_onset":
            line = f"[战斗开始] {e.text}"
        elif e.kind == "combat_end":
            line = f"[战斗结束] {e.text}"
        else:
            continue
        if include_event_ids and e.event_id:
            line = f"[e:{e.event_id}] {line}"
        lines.append(line)
    return "\n".join(lines)
