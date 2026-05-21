"""[2] Classify & Pair —— 把 Event 流转为 TaggedEvent（含 segments）。

设计：
- DM/PC/BOT 三种 source 走不同分类路径。
- PC 走纯规则三标记：`"..."` = pc_dialogue, `#`/`＃` = pc_action, `（）`/`()` = pc_ooc,
  `.r*`/`。r*` / `.init` = roll_cmd. 无标记裸文本 → unmarked_warning。
- DM 默认整段 dm_narration；行首是 `.r/。r/.init` → roll_cmd（DM 替 NPC 掷骰）。
- BOT 走模板识别：掷出了 / 设置如下 / 戏份结束 / 先攻列表 / 战斗一触即发 / 战斗结束。
- 配对：PC/DM 的 roll_cmd 与紧随其后第一条 bot 消息中对应 speaker 的 roll_result 关联。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from trpg2novel.parse.md_loader import Event

ROLL_CMD_RE = re.compile(r"^[.。](r\S*|init\s+\S+)(\s.*)?$", re.IGNORECASE)
IMAGE_LINE_RE = re.compile(r"^\[图(?:片|[:：]).*", re.DOTALL)
IMAGE_PLACEHOLDER_RE = re.compile(r"^\[\d+\]$")
IMAGE_META_RE = re.compile(r"^(?:资源[:：]|\\?-\s*image[:：])")

OOC_OPEN_TO_CLOSE = {"（": "）", "(": ")"}
OOC_OPEN_CHARS = set(OOC_OPEN_TO_CLOSE.keys())
OOC_CLOSE_CHARS = {"）", ")"}
ACTION_MARKERS = {"#", "＃"}
DIALOGUE_MARKER = '"'

BOT_ROLL_RE = re.compile(r"<(?P<who>[^>]+)>(?:掷出了|对.+?进行了|对先攻点数设置如下)")
TURN_MARKER_RE = re.compile(r"【[^】]+】戏份结束了")
INIT_CLEAR_RE = re.compile(r"先攻列表已清除")
INIT_LIST_RE = re.compile(r"先攻列表如下|当前先攻")
BATTLE_START_RE = re.compile(r"战斗一触即发|战斗.*开始")
BATTLE_END_RE = re.compile(r"战斗结束")


@dataclass
class Segment:
    kind: str
    text: str
    extra: dict = field(default_factory=dict)


@dataclass
class TaggedEvent:
    id: str
    timestamp: str
    speaker: str
    source: str  # dm / pc / bot / unknown
    body: str
    flags: dict[str, bool]
    segments: list[Segment]
    raw_lines: list[str] = field(default_factory=list)


@dataclass
class SessionConfig:
    session_id: str
    dm_handle: str
    bot_handles: list[str]
    player_handles: list[str]
    absent_players: list[str] = field(default_factory=list)
    date: str | None = None

    def source_of(self, speaker: str) -> str:
        if speaker == self.dm_handle:
            return "dm"
        if speaker in self.bot_handles:
            return "bot"
        if speaker in self.player_handles:
            return "pc"
        return "unknown"


def classify_events(events: Iterable[Event], config: SessionConfig) -> list[TaggedEvent]:
    tagged = [_classify_one(e, config) for e in events]
    _pair_rolls(tagged)
    return tagged


def _classify_one(event: Event, config: SessionConfig) -> TaggedEvent:
    source = config.source_of(event.speaker)
    if source == "pc":
        segments = _segment_pc(event.body)
    elif source == "dm":
        segments = _segment_dm(event.body)
    elif source == "bot":
        segments = _segment_bot(event.body, event.flags)
    else:
        segments = [Segment(kind="unknown", text=event.body)]
    return TaggedEvent(
        id=event.id,
        timestamp=event.timestamp,
        speaker=event.speaker,
        source=source,
        body=event.body,
        flags=event.flags,
        segments=segments,
        raw_lines=event.raw_lines,
    )


def _segment_pc(body: str) -> list[Segment]:
    """PC 消息：按行处理，每行用三标记 tokenizer 切。"""
    segments: list[Segment] = []
    for line in body.splitlines():
        line = line.rstrip()
        if not line:
            continue
        line_segs = _classify_special_line(line)
        if line_segs is not None:
            segments.extend(line_segs)
            continue
        segments.extend(_tokenize_pc_line(line))
    return segments


def _segment_dm(body: str) -> list[Segment]:
    """DM 消息：默认 dm_narration 整段；行首 .r/。r/.init 才是 roll_cmd。"""
    segments: list[Segment] = []
    narration_buf: list[str] = []

    def flush() -> None:
        if narration_buf:
            text = "\n".join(narration_buf).strip("\n")
            if text:
                segments.append(Segment(kind="dm_narration", text=text))
            narration_buf.clear()

    for line in body.splitlines():
        stripped = line.rstrip()
        if not stripped:
            narration_buf.append("")
            continue
        if ROLL_CMD_RE.match(stripped):
            flush()
            segments.append(
                Segment(
                    kind="roll_cmd",
                    text=stripped,
                    extra={"cmd_type": _roll_cmd_type(stripped)},
                )
            )
            continue
        special = _classify_special_line(stripped)
        if special is not None:
            flush()
            segments.extend(special)
            continue
        narration_buf.append(stripped)
    flush()
    return segments


def _segment_bot(body: str, flags: dict) -> list[Segment]:
    """骰娘消息：模板识别。"""
    text = body.strip()
    if flags.get("is_record_meta"):
        return [Segment(kind="record_meta", text=text)]
    if TURN_MARKER_RE.search(text):
        return [Segment(kind="turn_marker", text=text)]
    if INIT_CLEAR_RE.search(text):
        return [Segment(kind="initiative_clear", text=text)]
    if INIT_LIST_RE.search(text):
        return [Segment(kind="initiative_list", text=text)]
    if BATTLE_START_RE.search(text):
        return [Segment(kind="battle_marker", text=text, extra={"phase": "start"})]
    if BATTLE_END_RE.search(text):
        return [Segment(kind="battle_marker", text=text, extra={"phase": "end"})]
    m = BOT_ROLL_RE.search(text)
    if m:
        return [Segment(kind="roll_result", text=text, extra={"subject": m.group("who")})]
    return [Segment(kind="bot_state", text=text)]


def _classify_special_line(line: str) -> list[Segment] | None:
    """识别整行级别的非三标记内容：骰命令、图片、image meta。"""
    if ROLL_CMD_RE.match(line):
        cmd_type = _roll_cmd_type(line)
        seg = Segment(kind="roll_cmd", text=line, extra={"cmd_type": cmd_type})
        return [seg]
    if IMAGE_PLACEHOLDER_RE.match(line):
        return [Segment(kind="image", text=line)]
    if IMAGE_LINE_RE.match(line):
        return [Segment(kind="image", text=line)]
    if IMAGE_META_RE.match(line):
        return [Segment(kind="image_meta", text=line)]
    return None


def _roll_cmd_type(line: str) -> str:
    """把 roll_cmd 细分：dice / init_control / init_clear / init_list。"""
    m = re.match(r"^[.。]init\s+(\S+)", line, re.IGNORECASE)
    if m:
        sub = m.group(1).lower()
        if sub == "clr":
            return "init_clear"
        if sub.startswith("list") or sub == "ls":
            return "init_list"
        return "init_control"
    return "dice"


def _tokenize_pc_line(line: str) -> list[Segment]:
    """单行内 PC 三标记 tokenize。"""
    segments: list[Segment] = []
    pos = 0
    n = len(line)
    while pos < n:
        ch = line[pos]
        if ch.isspace():
            pos += 1
            continue
        if ch == DIALOGUE_MARKER:
            end = line.find(DIALOGUE_MARKER, pos + 1)
            if end == -1:
                segments.append(Segment(kind="pc_dialogue", text=line[pos + 1 :]))
                pos = n
            else:
                segments.append(Segment(kind="pc_dialogue", text=line[pos + 1 : end]))
                pos = end + 1
        elif ch in ACTION_MARKERS:
            stops = [
                p
                for p in (
                    line.find(DIALOGUE_MARKER, pos + 1),
                    line.find("（", pos + 1),
                    line.find("(", pos + 1),
                )
                if p != -1
            ]
            end = min(stops) if stops else n
            text = line[pos + 1 : end].rstrip()
            if text:
                segments.append(Segment(kind="pc_action", text=text))
            pos = end
        elif ch in OOC_OPEN_CHARS:
            close = OOC_OPEN_TO_CLOSE[ch]
            end = line.find(close, pos + 1)
            if end == -1:
                # 尝试匹配另一种闭括号
                alt = "）" if close == ")" else ")"
                end = line.find(alt, pos + 1)
            if end == -1:
                segments.append(Segment(kind="pc_ooc", text=line[pos + 1 :]))
                pos = n
            else:
                segments.append(Segment(kind="pc_ooc", text=line[pos + 1 : end]))
                pos = end + 1
        elif ch in OOC_CLOSE_CHARS:
            # 孤立的右括号 — 跳过，避免误归
            pos += 1
        else:
            mark_pos = _find_next_marker(line, pos + 1)
            end = mark_pos if mark_pos != -1 else n
            text = line[pos:end].strip()
            if text:
                segments.append(Segment(kind="unmarked_warning", text=text))
            pos = end
    return segments


def _find_next_marker(line: str, start: int) -> int:
    positions = [
        line.find(c, start)
        for c in (DIALOGUE_MARKER, "#", "＃", "（", "(")
    ]
    positions = [p for p in positions if p != -1]
    return min(positions) if positions else -1


def _pair_rolls(events: list[TaggedEvent]) -> None:
    """把 PC/DM 的 roll_cmd 与下一条匹配的 bot 结果关联。

    - cmd_type='dice'         → 匹配 bot roll_result（subject 等于发起者或包含 NPC 名）
    - cmd_type='init_clear'   → 匹配 bot initiative_clear
    - cmd_type='init_list'    → 匹配 bot initiative_list
    - cmd_type='init_control' → 不期待 bot 响应（fire-and-forget），跳过配对
    搜索窗口 8 条事件。
    """
    for i, ev in enumerate(events):
        if ev.source not in ("pc", "dm"):
            continue
        for seg_idx, seg in enumerate(ev.segments):
            if seg.kind != "roll_cmd":
                continue
            cmd_type = seg.extra.get("cmd_type", "dice")
            if cmd_type == "init_control":
                continue
            cmd_subject = ev.speaker
            tail = seg.text.split(maxsplit=1)
            npc_hint = tail[1].strip() if len(tail) > 1 else ""
            for j in range(i + 1, min(i + 8, len(events))):
                nxt = events[j]
                if nxt.source != "bot":
                    continue
                for r_idx, rseg in enumerate(nxt.segments):
                    matched = False
                    if cmd_type == "init_clear" and rseg.kind == "initiative_clear":
                        matched = True
                    elif cmd_type == "init_list" and rseg.kind == "initiative_list":
                        matched = True
                    elif cmd_type == "dice" and rseg.kind == "roll_result":
                        subj = rseg.extra.get("subject", "")
                        if subj == cmd_subject or (npc_hint and npc_hint in subj):
                            matched = True
                    if matched:
                        seg.extra["paired_result_event"] = nxt.id
                        seg.extra["paired_result_seg"] = r_idx
                        rseg.extra["paired_cmd_event"] = ev.id
                        rseg.extra["paired_cmd_seg"] = seg_idx
                        break
                if "paired_result_event" in seg.extra:
                    break


def tagged_to_json(events: list[TaggedEvent]) -> str:
    return json.dumps(
        [asdict(e) for e in events],
        ensure_ascii=False,
        indent=2,
    )


def save_tagged(events: list[TaggedEvent], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(tagged_to_json(events), encoding="utf-8")
