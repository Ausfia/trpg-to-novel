r"""[1] Parse 阶段 — 把 .md 跑团日志解析成结构化事件流。

输入：用户预处理过的 markdown 日志（UTF-8）
输出：events.json，每条 {id, timestamp, speaker, body, flags, raw_lines}

设计要点：
- 行头正则识别 `HH:MM:SS \<speaker\>: optional_inline`
- 一条消息直到下一个行头才结束（中间的空行 = 段落分隔，保留）
- 反转义 markdown 的 \< \> \[ \] \_ \@ \# \* \!
- 只识别和打标，**不删除任何内容**
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

# 行头：HH:MM:SS \<...\>: ?
# 第一组捕获时间戳，第二组捕获发言人（已转义的 \<\>），第三组捕获行内剩余内容（可空）
HEADER_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+\\<(.+?)\\>:\s*(.*)$")

# 反转义需要还原的字符
UNESCAPE_RE = re.compile(r"\\([<>\[\]_@#*!\"])")

# 标记类正则（用于在 flags 里打标，但不删除）
BOT_GREETING_PATTERNS = [
    re.compile(r"魔女(审判|在手).*现在(开庭|开启)"),  # 二阶堂希罗
    re.compile(r"记录已经开启"),
    re.compile(r"故事[\"「].+?[\"」]的记录已经继续开启"),
]
IMAGE_PLACEHOLDER_RE = re.compile(r"\[图[:：][^\]]+\]|\[\d+\]")


@dataclass
class Event:
    id: str
    timestamp: str
    speaker: str
    body: str
    flags: dict[str, bool] = field(default_factory=dict)
    raw_lines: list[str] = field(default_factory=list)


def unescape(text: str) -> str:
    """还原 .md 转义。"""
    return UNESCAPE_RE.sub(r"\1", text)


def parse_file(md_path: Path, session_id: str) -> list[Event]:
    """主入口：解析单份 .md 日志。"""
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    return list(parse_lines(lines, session_id))


def parse_lines(lines: Iterable[str], session_id: str) -> Iterable[Event]:
    current: Event | None = None
    body_lines: list[str] = []
    counter = 0

    def finalize() -> Event | None:
        nonlocal current, body_lines
        if current is None:
            return None
        # 去掉首尾空行，保留中间空行作为段落分隔
        body = "\n".join(body_lines).strip("\n")
        current.body = body
        current.flags = compute_flags(current.speaker, body)
        out = current
        current = None
        body_lines = []
        return out

    for line in lines:
        m = HEADER_RE.match(line)
        if m:
            done = finalize()
            if done is not None:
                yield done
            counter += 1
            ts, speaker_raw, inline = m.group(1), m.group(2), m.group(3)
            speaker = unescape(speaker_raw).strip()
            current = Event(
                id=f"{session_id}-{counter:04d}",
                timestamp=ts,
                speaker=speaker,
                body="",
                raw_lines=[line],
            )
            inline_unescaped = unescape(inline).rstrip()
            if inline_unescaped:
                body_lines.append(inline_unescaped)
        else:
            if current is None:
                # 文件头部的非行头内容（极少见，例如 .md 顶部备注）— 丢弃
                continue
            current.raw_lines.append(line)
            body_lines.append(unescape(line).rstrip())

    done = finalize()
    if done is not None:
        yield done


def compute_flags(speaker: str, body: str) -> dict[str, bool]:
    """打标但不删除——后续阶段决定如何处理。"""
    flags: dict[str, bool] = {}
    if any(p.search(body) for p in BOT_GREETING_PATTERNS):
        flags["is_record_meta"] = True
    if IMAGE_PLACEHOLDER_RE.search(body):
        flags["is_image_placeholder"] = True
    if body.strip() == "":
        flags["is_empty"] = True
    return flags


def events_to_json(events: list[Event]) -> str:
    return json.dumps(
        [asdict(e) for e in events],
        ensure_ascii=False,
        indent=2,
    )


def save_events(events: list[Event], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(events_to_json(events), encoding="utf-8")
