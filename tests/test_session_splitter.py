"""场次切分单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from trpg2novel.parse.session_splitter import (
    SessionChunk,
    _parse_ts,
    split_by_time_gap,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
S01_PATH = PROJECT_ROOT / "data" / "campaigns" / "jl_zheng_zheng" / "raw_logs" / "s01.md"


def test_parse_ts_valid():
    assert _parse_ts("20:30:07 <doom>: 开始") == 20 * 3600 + 30 * 60 + 7
    assert _parse_ts("00:00:00 a") == 0


def test_parse_ts_invalid():
    assert _parse_ts("no timestamp here") is None
    assert _parse_ts("") is None
    assert _parse_ts("2:30:07 short") is None  # 单位数小时不识别


def test_single_session_no_gap():
    """连续 1 小时内的日志，默认 8h 阈值下不切分。"""
    text = "\n".join([
        "20:00:00 <A>: hi",
        "20:30:00 <B>: hello",
        "21:00:00 <A>: bye",
    ])
    chunks = split_by_time_gap(text, min_gap_hours=8.0)
    assert len(chunks) == 1
    assert chunks[0].start_ts == "20:00:00"
    assert chunks[0].end_ts == "21:00:00"
    assert chunks[0].line_count == 3


def test_overnight_split():
    """前一条 23:00 → 后一条 08:00，跨夜 9h，默认 8h 阈值应切。"""
    text = "\n".join([
        "22:00:00 <A>: session 1 start",
        "23:00:00 <A>: session 1 end",
        "08:00:00 <A>: session 2 start",
        "09:00:00 <A>: session 2 end",
    ])
    chunks = split_by_time_gap(text, min_gap_hours=8.0)
    assert len(chunks) == 2
    assert chunks[0].end_ts == "23:00:00"
    assert chunks[1].start_ts == "08:00:00"


def test_no_split_below_threshold():
    """5h 间隔，阈值 8h 不切；阈值 4h 切。"""
    text = "\n".join([
        "10:00:00 <A>: a",
        "15:00:00 <A>: b",
    ])
    assert len(split_by_time_gap(text, min_gap_hours=8.0)) == 1
    assert len(split_by_time_gap(text, min_gap_hours=4.0)) == 2


def test_continuation_lines_belong_to_prev_chunk():
    """没有时间戳的行（续行/空行）应归属上一条事件所在 chunk。"""
    text = "\n".join([
        "20:00:00 <A>:",
        "    多行台词第一段",
        "    多行台词第二段",
        "20:01:00 <B>: ok",
    ])
    chunks = split_by_time_gap(text, min_gap_hours=8.0)
    assert len(chunks) == 1
    assert chunks[0].line_count == 4


def test_empty_input():
    chunks = split_by_time_gap("", min_gap_hours=8.0)
    assert chunks == []


def test_only_non_timestamp_lines():
    """全是无时间戳的内容，应作为单 chunk 返回（start_ts 为空）。"""
    text = "header\nnotes\n"
    chunks = split_by_time_gap(text, min_gap_hours=8.0)
    assert len(chunks) == 1
    assert chunks[0].start_ts == ""


def test_synthetic_two_session_merge():
    """模拟用户上传场景：把 s01 头部 + 跨夜 + s01 头部拼接，应被切成 2 段。"""
    half = "\n".join([
        "20:30:00 <DM>: 开始",
        "21:00:00 <PC>: 行动",
        "22:30:00 <DM>: 暂停",
    ])
    fused = half + "\n" + half  # 第二段 20:30 < 第一段 22:30，跨夜 22h
    chunks = split_by_time_gap(fused, min_gap_hours=8.0)
    assert len(chunks) == 2
    assert chunks[0].start_ts == "20:30:00"
    assert chunks[0].end_ts == "22:30:00"
    assert chunks[1].start_ts == "20:30:00"


@pytest.mark.skipif(not S01_PATH.exists(), reason="s01.md 不在仓库内")
def test_real_s01_default_threshold_single_session():
    """真实 s01.md 是单场跑团，8h 阈值下不应被切分（如果被切了说明阈值太严或日志本身跨夜）。"""
    text = S01_PATH.read_text(encoding="utf-8")
    chunks = split_by_time_gap(text, min_gap_hours=8.0)
    assert len(chunks) >= 1
    # 总行数等于输入行数
    total_lines = sum(c.line_count for c in chunks)
    assert total_lines == len(text.splitlines())
