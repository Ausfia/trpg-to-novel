"""[0] Session Splitter — 按时间差自动切分多场融合日志。

输入：单份 .md 跑团日志（行首有 HH:MM:SS 时间戳）。
逻辑：相邻两条事件的时间差 ≥ min_gap_hours 视为换场。
     跨夜（current < prev）按 +24h 处理，自然包含在 (curr - prev) % 86400 中。
输出：list[SessionChunk]，每段含起止时间戳与行片段。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\b")


@dataclass
class SessionChunk:
    """切分出的一段日志。"""

    index: int
    lines: list[str] = field(default_factory=list)
    start_ts: str = ""
    end_ts: str = ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def line_count(self) -> int:
        return len(self.lines)


def _parse_ts(line: str) -> int | None:
    m = _TS_RE.match(line)
    if not m:
        return None
    h, mi, s = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s


def split_by_time_gap(
    md_text: str,
    *,
    min_gap_hours: float = 8.0,
) -> list[SessionChunk]:
    """按时间差切分日志。

    Args:
        md_text: 整份 .md 文本。
        min_gap_hours: 切分阈值（小时）。相邻两条带时间戳的事件，时间差
            ≥ 此值即视为换场。默认 8 小时。跨夜按 +24h 计算。

    Returns:
        SessionChunk 列表（至少含一段）。
    """
    threshold_secs = int(min_gap_hours * 3600)
    lines = md_text.splitlines()

    chunks: list[SessionChunk] = []
    current = SessionChunk(index=0)
    last_ts_secs: int | None = None
    last_ts_str = ""

    for line in lines:
        ts_secs = _parse_ts(line)
        if ts_secs is None:
            current.lines.append(line)
            continue

        ts_str = line[:8]

        if last_ts_secs is not None:
            gap = (ts_secs - last_ts_secs) % 86400
            if gap >= threshold_secs and current.lines:
                # 收尾当前 chunk，开新的
                current.end_ts = last_ts_str
                chunks.append(current)
                current = SessionChunk(index=len(chunks))

        if not current.start_ts:
            current.start_ts = ts_str
        current.lines.append(line)
        last_ts_secs = ts_secs
        last_ts_str = ts_str

    if current.lines:
        current.end_ts = last_ts_str or current.start_ts
        chunks.append(current)

    return chunks
