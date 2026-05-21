"""[1] Parse 阶段：把 .md 跑团日志解析成结构化事件流。"""

from trpg2novel.parse.classify import (
    Segment,
    SessionConfig,
    TaggedEvent,
    classify_events,
    save_tagged,
)
from trpg2novel.parse.md_loader import Event, parse_file, parse_lines, save_events
from trpg2novel.parse.session_splitter import SessionChunk, split_by_time_gap

__all__ = [
    "Event",
    "Segment",
    "SessionChunk",
    "SessionConfig",
    "TaggedEvent",
    "classify_events",
    "parse_file",
    "parse_lines",
    "save_events",
    "save_tagged",
    "split_by_time_gap",
]
